"""Hook state and queued payloads belong to the org the requests act in.

Root cause guarded (Codex review C020): the hook identity used
``tokens.org_id or 'active'``. A sign-in with no pinned org (a Clerk OAuth
login) therefore got the same identity in every org, while its requests
carried different ``X-Org-Id`` values: a private transcript queued while acting
in org A could be flushed into org B after an org switch. The identity now
names the effective org (pinned, else explicit, else the server's active org,
looked up on every run) and nothing is sent or queued when it is unknown.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from beakr_sdk import Beakr
from beakr_sdk.credentials import StoredTokens, TokenStore

from beakr_cli import hook_queue
from beakr_cli.compounding import current_identity, import_and_compound
from beakr_cli.transcripts import ParsedSession

# These tests are about what capture sends, not whether a repository consents.
pytestmark = pytest.mark.usefixtures("capture_consented")

API = "https://api.test"
OK = {"conversation_id": "conv-1", "created": False, "stored": "summary"}


@pytest.fixture(autouse=True)
def oauth_login(monkeypatch):
    """A login with a known user and no pinned org, like a Clerk OAuth sign-in."""
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.delenv("BEAKR_API_KEY", raising=False)
    monkeypatch.delenv("BEAKR_ORG_ID", raising=False)
    TokenStore().save(
        StoredTokens(
            access_token="oat_1",
            refresh_token="rt_1",
            expires_at=4_000_000_000,
            base_url=API,
            user={"id": "user-1"},
        )
    )


def _session(session_id: str, *texts: str) -> ParsedSession:
    messages = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": t} for i, t in enumerate(texts)
    ]
    return ParsedSession(session_id, messages=messages)


def _run(session: ParsedSession):
    with Beakr(base_url=API) as sdk:
        return import_and_compound(
            sdk, session, client="claude-code", compound=False, queue_on_failure=True
        )


def _sent(route) -> list[tuple[str | None, dict]]:
    return [
        (call.request.headers.get("X-Org-Id"), json.loads(call.request.content))
        for call in route.calls
    ]


def _identity() -> str | None:
    with Beakr(base_url=API) as sdk:
        identity = current_identity(sdk)
    return None if identity is None else identity.principal


def test_the_identity_names_the_org_never_a_placeholder(monkeypatch) -> None:
    monkeypatch.setenv("BEAKR_ORG_ID", "org-A")
    assert _identity() == "user:user-1:org=org-A"
    monkeypatch.setenv("BEAKR_ORG_ID", "org-B")
    assert _identity() == "user:user-1:org=org-B"


def test_a_pinned_token_org_wins_over_an_explicit_header(monkeypatch) -> None:
    # The engine ignores X-Org-Id for an org-bound device token.
    TokenStore().save(
        StoredTokens(
            access_token="bk_at_1",
            refresh_token="bk_rt_1",
            expires_at=4_000_000_000,
            base_url=API,
            org_id="org-P",
            user={"id": "user-1"},
        )
    )
    monkeypatch.setenv("BEAKR_ORG_ID", "org-B")
    assert _identity() == "user:user-1:org=org-P"


@respx.mock
def test_an_explicit_org_switch_never_flushes_another_orgs_queue(monkeypatch) -> None:
    monkeypatch.setenv("BEAKR_ORG_ID", "org-A")
    respx.post(f"{API}/v1/conversations/import").mock(side_effect=httpx.ConnectError("down"))
    outcome = _run(_session("s-a", "private to org A"))
    assert outcome.queued and len(hook_queue.queued_files()) == 1

    monkeypatch.setenv("BEAKR_ORG_ID", "org-B")
    route = respx.post(f"{API}/v1/conversations/import").respond(json=OK)
    # respx keeps the failed org-A attempts (the SDK retries a keyed import
    # before the hook queues it) on the merged route; skip them.
    failed = len(route.calls)
    _run(_session("s-b", "work in org B"))

    sent = _sent(route)[failed:]
    assert sent, "the org-B session is sent"
    assert all(org == "org-B" for org, _ in sent)
    assert all(body["session_id"] == "s-b" for _, body in sent), "org A's payload was flushed"
    assert len(hook_queue.queued_files()) == 1, "org A's payload is kept for org A"


@respx.mock
def test_a_server_active_org_change_holds_the_queue_until_it_changes_back() -> None:
    active = respx.get(f"{API}/v1/me/organization")
    active.respond(json={"id": "org-A"})
    respx.post(f"{API}/v1/conversations/import").mock(side_effect=httpx.ConnectError("down"))
    assert _run(_session("s-a", "private to org A")).queued

    # The person switched their active org in the web app: same login, new org.
    active.respond(json={"id": "org-B"})
    route = respx.post(f"{API}/v1/conversations/import").respond(json=OK)
    failed = len(route.calls)  # the failed org-A attempts, kept on the merged route
    _run(_session("s-b", "work in org B"))
    assert [body["session_id"] for _, body in _sent(route)[failed:]] == ["s-b"]
    assert len(hook_queue.queued_files()) == 1

    # Back in org A, the queued payload is flushed with org A's identity.
    active.respond(json={"id": "org-A"})
    _run(_session("s-a2", "later in org A"))
    flushed = [body["session_id"] for _, body in _sent(route)[failed:]]
    assert "s-a" in flushed
    assert hook_queue.queued_files() == []


@respx.mock
def test_an_unknown_active_org_sends_and_queues_nothing() -> None:
    respx.get(f"{API}/v1/me/organization").mock(side_effect=httpx.ConnectError("offline"))
    route = respx.post(f"{API}/v1/conversations/import").respond(json=OK)
    outcome = _run(_session("s1", "a"))
    assert not route.called
    assert not outcome.imported and not outcome.queued
    assert hook_queue.queued_files() == []
