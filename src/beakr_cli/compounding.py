"""Import an agent session into Beakr and send it to the compounder.

Shared by ``beakr compound``, ``beakr conversations import`` and the session
hooks. Import is incremental: the engine keys a conversation on
(org, user, client, session_id). A per-session cursor in
``~/.beakr/hooks_state.json`` records how many messages have been sent (or
queued), with a hash of each; each run sends only the messages after it, with
``base_index`` set to the cursor so the engine places them after the stored
ones. Sending a tail WITHOUT ``base_index`` would be read as the whole
transcript and replace the stored history with just that tail. When the engine
holds fewer messages than the cursor (a refused or lost earlier batch), it
answers 409 with the count it holds and the run resends from there.

A transcript can also be rewritten under the cursor: compacted (shorter), or a
message edited in place (same length, different content). The stored hashes
catch both, and the run then sends the whole transcript WITHOUT ``base_index``;
the engine keeps the prefix whose hashes still match and replaces the rest.
Sends are split into batches below the engine's per-import cap.

Hook state and queued imports belong to one sign-in: they are keyed by the API
origin and the signed-in principal (:class:`HookIdentity`), so a login switch
can never upload a queued private transcript with someone else's credentials.

Claude Code's ``Stop`` hook fires at the end of every assistant turn, not once
per session, so compounding is also throttled per session; the ``SessionEnd``
hook passes ``force`` to flush the last turns. In hook mode a failed import is
queued (``hook_queue``) instead of raised, and queued imports are sent first on
the next run so a session's messages arrive in order.
"""

from __future__ import annotations

import contextlib
import hashlib
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from beakr_sdk import Beakr
from beakr_sdk.credentials import beakr_home
from beakr_sdk.errors import (
    APIConnectionError,
    AuthenticationError,
    BeakrError,
    RateLimitError,
    ServerError,
)
from beakr_sdk.resources.conversations import expected_base_index

from beakr_cli import hook_queue
from beakr_cli.hook_runtime import log_hook, read_state, write_state
from beakr_cli.project_binding import (
    capture_enabled,
    learning_enabled,
    read_binding,
    resolve_project,
)
from beakr_cli.transcripts import HUMAN, ParsedSession, codex_session_files, parse_codex_session

HOOKS_STATE_FILE = "hooks_state.json"
CODEX_SESSIONS_STATE_FILE = "codex_sessions.json"
DEFAULT_MIN_INTERVAL_SECONDS = 900
#: Messages per import request; the engine refuses more than 2000 in one.
MAX_BATCH_MESSAGES = 1000
#: Hook-state key holding the principal looked up for a sign-in that does not
#: carry its user id (keyed by a hash of the access token).
_IDENTITY_CACHE_KEY = "_identities"

#: What ``POST /v1/compound`` answers when the engine holds no unread text.
NOTHING_TO_COMPOUND = "nothing_to_compound"

#: Failures worth retrying later. Anything else (a 4xx on the body) would fail
#: the same way forever, so a queued payload hitting it is dropped and logged.
_RETRYABLE = (APIConnectionError, ServerError, RateLimitError, AuthenticationError)


@dataclass
class CompoundOutcome:
    session_id: str
    conversation_id: str | None
    imported: bool
    compounded: bool
    reason: str | None = None
    new_messages: int = 0
    queued: bool = False
    #: human / headless / unknown, and what the imports sent as ``interactive``.
    classification: str | None = None
    interactive: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "conversation_id": self.conversation_id,
            "imported": self.imported,
            "compounded": self.compounded,
            "reason": self.reason,
            "new_messages": self.new_messages,
            "queued": self.queued,
            "classification": self.classification,
            "interactive": self.interactive,
        }


@dataclass(frozen=True)
class HookIdentity:
    """Who a hook run is signed in as: the API origin and the principal.

    The principal is what the engine would resolve the credential to, without
    asking it when the credential already says: an API key is pinned to one
    user and org, so a hash of the key names them; a device login names its
    org and, once looked up, its user; a dev identity names its user.
    """

    origin: str
    principal: str
    # The org every request of the run must explicitly target; None for an API
    # key, which the engine pins to its own org whatever the header says.
    org: str | None = None

    def key(self, client: str, session_id: str) -> str:
        return f"{self.origin}|{self.principal}|{client}:{session_id}"

    def as_dict(self) -> dict[str, str]:
        return {"origin": self.origin, "principal": self.principal}


def _fingerprint(*parts: str) -> str:
    return hashlib.sha256("\x00".join(parts).encode()).hexdigest()[:24]


def current_identity(sdk: Beakr) -> HookIdentity | None:
    """The identity this run's state and queue belong to; None when unknown.

    Unknown (no credential, the user lookup failed offline, or the org the
    requests would act in cannot be established) means nothing is sent or
    queued this run. The transcript stays the source of truth, so the messages
    go out on a later run.

    The org is the one the requests will actually run in, never a placeholder:
    a payload queued while acting in org A must not be flushed into org B
    after an org switch (audit C020).
    """
    try:
        credential = sdk.credential
    except BeakrError:
        return None
    origin = sdk.origin
    if credential.dev_headers:
        user = credential.dev_headers.get("X-Identity-Id", "")
        org = sdk.org_id or credential.dev_headers.get("X-Org-Id") or _server_active_org(sdk)
        if not org:
            return None
        return HookIdentity(origin, f"dev:{user}:org={org}", org=str(org))
    tokens = credential.tokens
    if tokens is None:
        # An API key is pinned to one user and org (X-Org-Id is ignored for it),
        # so the key itself names the org.
        return HookIdentity(origin, f"key:{_fingerprint(origin, credential.token or '')}")
    user_id = (tokens.user or {}).get("id")
    if not user_id:
        user_id = _cached_user_id(sdk, origin, tokens.access_token)
    if not user_id:
        return None
    org = effective_org(sdk, tokens.org_id)
    if not org:
        return None
    return HookIdentity(origin, f"user:{user_id}:org={org}", org=org)


def effective_org(sdk: Beakr, pinned_org: str | None) -> str | None:
    """The org a token's requests run in: pinned, else explicit, else the server's.

    A Beakr device token is bound to the org it was approved in; the engine
    ignores ``X-Org-Id`` for it. A token without a pinned org (Clerk OAuth)
    runs in the org sent as ``X-Org-Id`` (the SDK's ``org_id`` /
    ``BEAKR_ORG_ID``), and with neither, in the person's active org on the
    server. That last one can change between runs, so it is looked up on every
    run rather than cached: a payload queued while it was A is then held back
    once it is B. None (lookup failed) means fail closed.
    """
    if pinned_org:
        return str(pinned_org)
    if sdk.org_id:
        return str(sdk.org_id)
    return _server_active_org(sdk)


@contextlib.contextmanager
def pinned_org(sdk: Beakr, org: str | None) -> Iterator[None]:
    """Send ``org`` as ``X-Org-Id`` on every request of this run, then restore.

    The identity's org was resolved once (possibly from the server's active
    org). Without the pin, a later request in the same run would act in
    whatever org is active on the server by then, while the cursor and queue
    still say the first one (audit C020 follow-up). Queue flushes, imports,
    compounds and retries all go through ``sdk``, so all of them carry it.
    """
    if org is None:
        yield
        return
    previous = sdk.org_id
    sdk.org_id = org
    try:
        yield
    finally:
        sdk.org_id = previous


def _server_active_org(sdk: Beakr) -> str | None:
    try:
        org = sdk.me.active_org()
    except BeakrError as exc:
        log_hook(f"could not look up the active organization ({type(exc).__name__}); nothing sent")
        return None
    org_id = (org or {}).get("id") if isinstance(org, dict) else None
    return str(org_id) if org_id else None


def _cached_user_id(sdk: Beakr, origin: str, access_token: str) -> str | None:
    cache_key = _fingerprint(origin, access_token)
    state = read_state(HOOKS_STATE_FILE)
    cache = state.get(_IDENTITY_CACHE_KEY)
    cache = cache if isinstance(cache, dict) else {}
    if cache_key in cache:
        return str(cache[cache_key])
    try:
        me = sdk.me.get()
    except BeakrError as exc:
        log_hook(f"could not look up the signed-in user ({type(exc).__name__}); nothing sent")
        return None
    user_id = ((me or {}).get("user") or {}).get("id")
    if not user_id:
        return None
    # One entry per access token; tokens rotate hourly, so keep the cache small.
    cache = dict(list(cache.items())[-20:])
    cache[cache_key] = str(user_id)
    state[_IDENTITY_CACHE_KEY] = cache
    write_state(HOOKS_STATE_FILE, state)
    return str(user_id)


def message_hash(message: dict[str, Any]) -> str:
    """The same digest the engine keeps per message: role and content."""
    raw = f"{message.get('role', '')}\x00{message.get('content', '')}"
    return hashlib.sha256(raw.encode()).hexdigest()


def history_rewritten(messages: list[dict[str, Any]], entry: dict[str, Any]) -> bool:
    """True when messages already sent were compacted away or edited in place."""
    cursor = int(entry.get("cursor", 0))
    if cursor > len(messages):
        return True
    stored = entry.get("hashes")
    if not isinstance(stored, list) or len(stored) != cursor:
        # State from before hashes were kept: nothing to compare against.
        return False
    return [message_hash(m) for m in messages[:cursor]] != stored


def project_for_session(explicit: str | None, cwd: str | None) -> str | None:
    """Explicit flag, then the binding of the directory the session ran in, then defaults.

    The session's own cwd matters more than this process's: a Codex sweep runs
    from anywhere but each session belongs to the repository it was opened in.
    """
    if explicit and explicit != "auto":
        return explicit
    if cwd:
        binding = read_binding(Path(cwd))
        if binding and binding.get("project_id"):
            return str(binding["project_id"])
    return resolve_project(None)


@contextlib.contextmanager
def _capture_lock() -> Iterator[None]:
    """Serialize capture runs: overlapping Stop hooks would otherwise read the
    same cursor and send the same messages twice."""
    try:
        import fcntl
    except ImportError:  # pragma: no cover - Windows has no flock
        yield
        return
    path = beakr_home() / "hooks.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _entry(state: dict[str, Any], key: str) -> dict[str, Any]:
    entry = state.get(key)
    return entry if isinstance(entry, dict) else {}


def _save_entry(key: str, **updates: Any) -> dict[str, Any]:
    state = read_state(HOOKS_STATE_FILE)
    entry = {**_entry(state, key), **updates}
    state[key] = entry
    write_state(HOOKS_STATE_FILE, state)
    return entry


_BINDING_IDENTITY_FIELDS = ("project_id", "base_url", "org_id")


def _binding_identity(cwd: str | None) -> dict[str, Any]:
    """The project, API and organization a directory's binding names right now."""
    binding = read_binding(Path(cwd)) if cwd else None
    return {field: (binding or {}).get(field) for field in _BINDING_IDENTITY_FIELDS}


def _queued_capture_problem(queued: dict[str, Any]) -> str | None:
    """Why a queued automatic capture may no longer be sent, or None when it may.

    A payload is authorized for the project its consent was given to: the
    binding recorded when it was queued, or, for entries queued before that
    was recorded, the project its body names. It is sent only while the
    directory still consents AND is still bound to that same project (and,
    when recorded, the same API and organization). An entry that names no
    project at all cannot be attributed and is dropped.
    """
    cwd = queued.get("cwd")
    if not capture_enabled(cwd):
        return "capture is off for its project"
    recorded = queued.get("binding") if isinstance(queued.get("binding"), dict) else {}
    body_project = (queued.get("body") or {}).get("project_id")
    project = recorded.get("project_id") or body_project
    if not project:
        return "it records no project it was captured for"
    if body_project and body_project != project:
        return "its payload names a different project than the one it was captured for"
    current = _binding_identity(cwd)
    if current["project_id"] != project:
        return "its directory is now bound to a different project"
    for field in ("base_url", "org_id"):
        if field in recorded and recorded[field] != current[field]:
            return f"its directory's binding changed {field} since it was captured"
    return None


def session_interactive(session: ParsedSession) -> bool:
    """Whether an automatic import may tell the engine a person typed this session.

    Both are required: the project opted in to learning from sessions
    (``learn_from_sessions: true``, separate from capture consent), and the
    host's own markers classify the session as ``human``. Headless runs
    (``claude -p``, the Agent SDK, ``codex exec``, subagents) and sessions
    with no markers are sent as not interactive, so personal learning never
    reads them.
    """
    return session.classification == HUMAN and learning_enabled(session.cwd)


def _queued_interactive(queued: dict[str, Any]) -> bool:
    """What a queued retry sends as ``interactive``: never more than at capture.

    True only when the batch was queued as interactive, its classification
    was recorded as human, and the directory still opts in to learning. An
    entry queued before classification was recorded is sent as false.
    """
    body = queued.get("body") or {}
    return (
        body.get("interactive") is True
        and queued.get("classification") == HUMAN
        and learning_enabled(queued.get("cwd"))
    )


def flush_queue(sdk: Beakr, identity: HookIdentity) -> bool:
    """Send this sign-in's queued imports, oldest first; True when none of them remain.

    A payload queued under another sign-in (a login or org switch since) is
    kept and skipped, never sent with the current credentials; ``doctor``
    reports it. A payload with no recorded sign-in predates identity binding
    and cannot be attributed, so it is dropped (its transcript remains).
    """
    for path in hook_queue.queued_files():
        queued = hook_queue.read_payload(path)
        if queued is None or not isinstance(queued.get("body"), dict):
            log_hook(f"hook queue: dropping unreadable {path.name}")
            path.unlink(missing_ok=True)
            continue
        owner = queued.get("identity")
        if not isinstance(owner, dict):
            log_hook(f"hook queue: dropping {path.name}, it records no sign-in")
            path.unlink(missing_ok=True)
            continue
        if owner != identity.as_dict():
            log_hook(f"hook queue: {path.name} belongs to another sign-in; kept, not sent")
            continue
        # Only automatic capture queues. The consent that authorized it must
        # still hold for the same project, API and organization: consent given
        # later to whatever the directory is bound to now never covers a
        # payload captured for another project (audit C073).
        problem = _queued_capture_problem(queued)
        if problem is not None:
            log_hook(f"hook queue: dropping {path.name}, {problem}")
            path.unlink(missing_ok=True)
            continue
        key = str(queued.get("key") or "")
        body = {**queued["body"], "interactive": _queued_interactive(queued)}
        try:
            result = sdk.conversations.import_(**body)
        except _RETRYABLE as exc:
            log_hook(f"hook queue: still failing ({type(exc).__name__}); {path.name} kept")
            return False
        except BeakrError as exc:
            expected = expected_base_index(exc)
            if expected is not None and key:
                # The engine holds fewer messages than this batch assumed. The
                # transcript is the source of truth: rewind the cursor so the
                # next run resends from what the engine holds.
                log_hook(f"hook queue: {key} behind on the server; resend from {expected}")
                _save_entry(key, cursor=expected, hashes=_entry_hashes(key)[:expected])
            else:
                log_hook(f"hook queue: dropping {path.name}, the API refused it: {exc}")
            path.unlink(missing_ok=True)
            continue
        path.unlink(missing_ok=True)
        if key:
            entry = _entry(read_state(HOOKS_STATE_FILE), key)
            _save_entry(
                key,
                conversation_id=str(result["conversation_id"]),
                imported_count=int(entry.get("imported_count", 0))
                + len(body.get("messages") or []),
            )
    return True


def _entry_hashes(key: str) -> list[str]:
    hashes = _entry(read_state(HOOKS_STATE_FILE), key).get("hashes")
    return list(hashes) if isinstance(hashes, list) else []


def _bodies(
    base_body: dict[str, Any],
    messages: list[dict[str, Any]],
    start: int,
    *,
    full: bool,
    compound_last: bool = False,
) -> list[dict[str, Any]]:
    """Import bodies that send ``messages[start:]`` in batches below the engine's cap.

    ``full`` sends the transcript as a whole: the first batch goes without
    ``base_index`` (the engine keeps the prefix whose hashes match and replaces
    the rest), and later batches append after it. ``compound_last`` asks the
    last batch to queue the capture, so it starts once every batch is stored.
    """
    bodies: list[dict[str, Any]] = []
    position = start
    while position < len(messages):
        chunk = messages[position : position + MAX_BATCH_MESSAGES]
        body = {**base_body, "messages": chunk}
        if not (full and position == start):
            body["base_index"] = position
        bodies.append(body)
        position += len(chunk)
    if compound_last and bodies:
        bodies[-1]["compound"] = True
    return bodies


@dataclass
class _Sent:
    result: Any = None
    #: Bodies not sent because the API was unreachable or overloaded.
    unsent: list[dict[str, Any]] | None = None
    error: BeakrError | None = None


def _send(
    sdk: Beakr,
    bodies: list[dict[str, Any]],
    *,
    base_body: dict[str, Any],
    messages: list[dict[str, Any]],
    key: str,
    compound_last: bool,
) -> _Sent:
    """Send ``bodies`` in order, stopping at the first retryable failure.

    A 409 naming the stored count re-syncs once: the engine holds fewer
    messages than assumed (an earlier batch was refused or the conversation was
    deleted), and ``messages`` is the whole local transcript, so the gap is
    filled from it. Any other refusal is raised.
    """
    sent = _Sent()
    pending = list(bodies)
    resynced = False
    while pending:
        try:
            sent.result = sdk.conversations.import_(**pending[0])
        except _RETRYABLE as exc:
            sent.unsent, sent.error = pending, exc
            return sent
        except BeakrError as exc:
            expected = expected_base_index(exc)
            if resynced or expected is None or expected > len(messages):
                raise
            resynced = True
            log_hook(f"{key}: server holds {expected} message(s); resending from there")
            pending = _bodies(
                base_body, messages, expected, full=False, compound_last=compound_last
            )
            continue
        pending.pop(0)
    return sent


def import_and_compound(
    sdk: Beakr,
    session: ParsedSession,
    *,
    client: str,
    project: str | None = None,
    force: bool = False,
    min_interval: float = DEFAULT_MIN_INTERVAL_SECONDS,
    compound: bool = True,
    queue_on_failure: bool = False,
    store_full_text: bool | None = None,
    require_consent: bool = False,
    now: float | None = None,
    summary: str | None = None,
    interactive: bool | None = None,
) -> CompoundOutcome:
    """Send the messages after this session's cursor, then compound unless throttled.

    ``require_consent`` is set by every automatic path (hooks, their detached
    child, the Codex wrapper's post-session sweep): the session's repository
    must have opted in (``capture: true`` in its ``.beakr/project.json``),
    or nothing is sent and nothing is queued. A deliberate import passes False.

    ``interactive`` is what every batch tells the engine about whether a
    person typed the session, which decides whether personal learning may
    read it. ``None`` applies the automatic rule (``session_interactive``);
    ``beakr conversations import`` passes the person's explicit choice.
    """
    if interactive is None:
        interactive = session_interactive(session)
    if require_consent and not capture_enabled(session.cwd):
        outcome = CompoundOutcome(session.session_id, None, False, False)
        outcome.reason = "capture is off for this project; nothing sent"
        log_hook(
            f"{client}:{session.session_id}: {outcome.reason} (cwd {session.cwd or 'unknown'})"
        )
        return outcome
    with _capture_lock():
        identity = current_identity(sdk)
        if identity is None:
            outcome = CompoundOutcome(session.session_id, None, False, False)
            outcome.reason = "signed-in identity unknown; nothing sent"
            log_hook(f"{client}:{session.session_id}: {outcome.reason}")
            return outcome
        with pinned_org(sdk, identity.org):
            return _import_and_compound_locked(
                sdk,
                identity,
                session,
                client=client,
                project=project,
                force=force,
                min_interval=min_interval,
                compound=compound,
                queue_on_failure=queue_on_failure,
                store_full_text=store_full_text,
                now=time.time() if now is None else now,
                summary=summary,
                interactive=interactive,
            )


def _import_and_compound_locked(
    sdk: Beakr,
    identity: HookIdentity,
    session: ParsedSession,
    *,
    client: str,
    project: str | None,
    force: bool,
    min_interval: float,
    compound: bool,
    queue_on_failure: bool,
    store_full_text: bool | None,
    now: float,
    summary: str | None = None,
    interactive: bool = False,
) -> CompoundOutcome:
    outcome = CompoundOutcome(session.session_id, None, False, False)
    outcome.classification = session.classification
    outcome.interactive = interactive
    key = identity.key(client, session.session_id)
    messages = session.messages
    # Decide on a rewrite before flushing: this session's queued batches belong
    # to the history being replaced, and sending them first would only put it
    # back for the full resend to undo.
    rewritten = history_rewritten(messages, _entry(read_state(HOOKS_STATE_FILE), key))
    if rewritten:
        log_hook(f"{key}: transcript rewritten under the cursor; resending it whole")
        hook_queue.drop_for_key(key)
    queue_clear = flush_queue(sdk, identity) if queue_on_failure else True

    entry = _entry(read_state(HOOKS_STATE_FILE), key)
    outcome.conversation_id = entry.get("conversation_id")
    start = 0 if rewritten else int(entry.get("cursor", 0))
    hashes = [message_hash(m) for m in messages]
    outcome.new_messages = len(messages) - start
    # Decided before sending so the import itself can ask for the capture.
    throttled = _throttled(entry, force=force, min_interval=min_interval, now=now)

    if outcome.new_messages > 0:
        base_body = {
            "client": client,
            "session_id": session.session_id,
            "files_touched": session.files_touched or None,
            "project_id": project_for_session(project, session.cwd),
            "title": session.title,
            "interactive": interactive,
        }
        if store_full_text is not None:
            # Left out, the engine applies the person's full-text setting for
            # this client (PROSE-392); sent, it overrides that setting.
            base_body["store_full_text"] = store_full_text
        if session.tools_used:
            # References kept with the summary: the tools the session called and
            # the Beakr Knowledge it read, at the version read (freshness).
            base_body["tools_used"] = session.tools_used
        if session.sources_read:
            base_body["sources_read"] = session.sources_read
        if summary:
            # What the default storage keeps when the transcript itself is not
            # stored (summary plus references, PROSE-392).
            base_body["summary"] = summary
        if compound:
            # The engine keeps message text only by opt-in. A session meant to
            # be compounded, now or after the throttle, holds each batch's text
            # for the capture (bounded by the engine's deadline) without
            # queuing one per batch.
            base_body["hold_text"] = True
        # Compounding now: the last batch imports and queues the capture in
        # one request.
        compound_last = compound and not throttled
        bodies = _bodies(base_body, messages, start, full=rewritten, compound_last=compound_last)
        if queue_clear:
            sent = _send(
                sdk,
                bodies,
                base_body=base_body,
                messages=messages,
                key=key,
                compound_last=compound_last,
            )
        else:
            # Older imports are still queued; these go behind them, in order.
            sent = _Sent(unsent=bodies)
        if not sent.unsent:
            entry = _save_entry(
                key,
                cursor=len(messages),
                hashes=hashes,
                imported_count=int(entry.get("imported_count", 0)) + outcome.new_messages,
                conversation_id=str(sent.result["conversation_id"]),
                imported_at=now,
            )
            outcome.imported = True
            outcome.conversation_id = entry["conversation_id"]
        elif not queue_on_failure:
            # Outside a hook the caller sees the failure; the cursor is unchanged,
            # so a rerun resends from it and the engine keeps what already arrived.
            assert sent.error is not None
            raise sent.error
        elif not hook_queue.can_enqueue(len(sent.unsent)):
            outcome.reason = "import failed and the retry queue is full"
            return outcome
        else:
            for body in sent.unsent:
                hook_queue.enqueue(
                    {
                        "key": key,
                        "identity": identity.as_dict(),
                        # The retry keeps the text hold but not the capture
                        # request: the run after the flush compounds, under
                        # the throttle that applies then.
                        "body": {k: v for k, v in body.items() if k != "compound"},
                        # Re-checked before every retry: consent withdrawn since
                        # the failure means the retry is dropped, not sent.
                        "cwd": session.cwd,
                        # What the consent was given to; a later rebinding of
                        # this directory to another project, API or org does
                        # not carry this payload over (C073).
                        "binding": _binding_identity(session.cwd),
                        # Decided once, at capture time: a retry never sends
                        # a session as more interactive than this.
                        "classification": session.classification,
                    }
                )
            progress: dict[str, Any] = {"cursor": len(messages), "hashes": hashes}
            if sent.result is not None:
                progress["conversation_id"] = str(sent.result["conversation_id"])
            entry = _save_entry(key, **progress)
            outcome.queued = True
            outcome.reason = "import queued for retry"
            return outcome
    elif rewritten:
        # Everything already sent was compacted away; nothing to resend.
        entry = _save_entry(key, cursor=len(messages), hashes=hashes)

    if not compound:
        outcome.reason = outcome.reason or "import only"
        return outcome
    conversation_id = entry.get("conversation_id")
    imported_count = int(entry.get("imported_count", 0))
    if not conversation_id or imported_count <= int(entry.get("compounded_count", 0)):
        outcome.reason = outcome.reason or "nothing new to compound"
        return outcome
    if throttled:
        outcome.reason = "compounded recently"
        return outcome
    # Idempotent after an import that already queued it; either way the answer
    # says whether a capture is actually queued.
    answer = sdk.engine.compound(str(conversation_id))
    status = answer.get("status") if isinstance(answer, dict) else None
    if status == NOTHING_TO_COMPOUND:
        # The engine holds no unread text (its hold expired, or the messages
        # were imported without one). Not compounded, so the counters stay.
        outcome.reason = "the engine holds no unread text to compound"
        log_hook(f"{key}: {outcome.reason}")
        return outcome
    _save_entry(key, compounded_at=now, compounded_count=imported_count)
    outcome.compounded = True
    return outcome


def _throttled(entry: dict[str, Any], *, force: bool, min_interval: float, now: float) -> bool:
    """Whether this session compounded too recently to compound again now."""
    last = entry.get("compounded_at")
    return not force and isinstance(last, (int, float)) and now - last < min_interval


def compound_codex_sessions(
    sdk: Beakr,
    codex_home: Path,
    *,
    project: str | None = None,
    since_hours: float | None = None,
    require_consent: bool = False,
    now: float | None = None,
) -> list[CompoundOutcome]:
    """Import and compound each Codex session file that is new or changed since last run.

    ``require_consent`` (the ``beakr codex`` wrapper's automatic sweep) skips
    sessions whose repository has not opted in to capture.
    """
    current = time.time() if now is None else now
    seen = read_state(CODEX_SESSIONS_STATE_FILE)
    outcomes: list[CompoundOutcome] = []
    for path in codex_session_files(codex_home):
        mtime = path.stat().st_mtime
        if since_hours is not None and current - mtime > since_hours * 3600:
            continue
        if seen.get(str(path)) == mtime:
            continue
        session = parse_codex_session(path)
        # Each sweep handles a changed file once, so no throttle applies here;
        # the session cursor still keeps a re-sweep from resending old messages.
        outcome = import_and_compound(
            sdk,
            session,
            client="codex",
            project=project,
            force=True,
            require_consent=require_consent,
            now=current,
        )
        outcomes.append(outcome)
        seen[str(path)] = mtime
        # Saved after each file so a failure part-way keeps the progress made.
        write_state(CODEX_SESSIONS_STATE_FILE, seen)
    return outcomes
