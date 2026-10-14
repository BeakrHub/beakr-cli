"""Every request of a hook run targets the org its identity was resolved for.

Root cause guarded (Codex review C020 follow-up): the identity looked up the
server's active org once, but the requests that followed sent no ``X-Org-Id``.
If the active org changed in another client between that lookup and the
import, the import acted in org B while the cursor and queue said org A, and a
queued private transcript recorded for A could land in B. The resolved org is
now pinned on the client for the whole run: queue flushes, imports, retries
and the compound call all carry it explicitly.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from beakr_sdk import Beakr
from beakr_sdk.credentials import StoredTokens, TokenStore

from beakr_cli import hook_queue
from beakr_cli.compounding import import_and_compound
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


def _active_org_switches_after_lookup(first: str, then: str):
    """The active-org GET answers ``first`` once, then the server default is ``then``."""
    answers = iter([first])

    def respond(_request):
        return httpx.Response(200, json={"id": next(answers, then)})

    return respx.get(f"{API}/v1/me/organization").mock(side_effect=respond)


@respx.mock
def test_every_request_of_the_run_targets_the_resolved_org() -> None:
    # A payload queued earlier while acting in org A.
    respx.get(f"{API}/v1/me/organization").respond(json={"id": "org-A"})
    respx.post(f"{API}/v1/conversations/import").mock(side_effect=httpx.ConnectError("down"))
    with Beakr(base_url=API) as sdk:
        assert import_and_compound(
            sdk,
            _session("s-q", "queued in org A"),
            client="claude-code",
            compound=False,
            queue_on_failure=True,
        ).queued
    assert len(hook_queue.queued_files()) == 1

    # This run resolves org A, then another client switches the active org to B.
    respx.routes.clear()
    _active_org_switches_after_lookup("org-A", "org-B")
    imports = respx.post(f"{API}/v1/conversations/import").respond(json=OK)
    compounds = respx.post(f"{API}/v1/compound").respond(
        json={"status": "queued", "conversation_id": "conv-1"}
    )
    with Beakr(base_url=API) as sdk:
        outcome = import_and_compound(
            sdk,
            _session("s-new", "new work", "reply"),
            client="claude-code",
            force=True,
            compound=True,
            queue_on_failure=True,
        )
        assert sdk.org_id is None, "the pin is restored after the run"

    assert outcome.imported
    sent = [call.request for call in imports.calls]
    assert len(sent) == 2, "the queued flush and the new session"
    assert [r.headers.get("X-Org-Id") for r in sent] == ["org-A", "org-A"]
    assert compounds.called
    assert all(c.request.headers.get("X-Org-Id") == "org-A" for c in compounds.calls)
    assert hook_queue.queued_files() == []


@respx.mock
def test_a_retried_import_keeps_the_resolved_org() -> None:
    _active_org_switches_after_lookup("org-A", "org-B")
    attempts = iter(
        [
            httpx.Response(
                429, headers={"Retry-After": "0"}, json={"error": {"code": "rate_limited"}}
            ),
            httpx.Response(200, json=OK),
        ]
    )
    imports = respx.post(f"{API}/v1/conversations/import").mock(
        side_effect=lambda _request: next(attempts)
    )
    with Beakr(base_url=API) as sdk:
        outcome = import_and_compound(
            sdk, _session("s-r", "retry me"), client="claude-code", compound=False
        )
    assert outcome.imported
    assert len(imports.calls) == 2
    assert all(c.request.headers.get("X-Org-Id") == "org-A" for c in imports.calls)


@respx.mock
def test_an_explicit_org_is_kept_and_restored() -> None:
    imports = respx.post(f"{API}/v1/conversations/import").respond(json=OK)
    with Beakr(base_url=API, org_id="org-X") as sdk:
        import_and_compound(sdk, _session("s-x", "explicit"), client="claude-code", compound=False)
        assert sdk.org_id == "org-X"
    assert all(c.request.headers.get("X-Org-Id") == "org-X" for c in imports.calls)
