"""CLI behavior that follows engine changes: Clerk sign-in, compound states,
redelivery conflicts, export step-up, and the org audit MCP tool."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from beakr_sdk.credentials import StoredTokens, TokenStore
from typer.testing import CliRunner

from beakr_cli.main import internal_app as app

API = "https://api.test"
CLERK = "https://clerk.example.test"
CLERK_CONFIG = {
    "issuer": "clerk",
    "device_authorization_endpoint": f"{CLERK}/oauth/device_authorization",
    "token_endpoint": f"{CLERK}/oauth/token",
    "client_id": "cli-client",
}


def _refusal(status: int, code: str) -> httpx.Response:
    return httpx.Response(
        status,
        json={
            "detail": {"code": code, "message": "Refused."},
            "error": {"code": code, "message": "Refused."},
        },
    )


# -- Clerk sign-in -------------------------------------------------------------


def _save_clerk_tokens() -> None:
    TokenStore().save(
        StoredTokens(
            access_token="oat_1",
            refresh_token="rt_1",
            expires_at=4_000_000_000.0,
            base_url=API,
            issuer="clerk",
            token_endpoint=CLERK_CONFIG["token_endpoint"],
            oauth_client_id="cli-client",
        )
    )


def test_status_names_the_clerk_issuer(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    _save_clerk_tokens()
    result = CliRunner().invoke(app, ["auth", "status", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["issuer"] == "clerk"


@respx.mock
def test_doctor_explains_a_clerk_token_without_beakr_scopes(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    _save_clerk_tokens()
    respx.get(f"{API}/v1/public/openapi.json").respond(200, json={})
    respx.get(f"{API}/v1/me").respond(json={"user": {"display_name": "Ada"}})
    respx.get(f"{API}/v1/projects").respond(
        403,
        json={
            "detail": {"code": "insufficient_scope"},
            "error": {
                "code": "insufficient_scope",
                "message": "This credential lacks the 'projects:read' scope.",
                "required_scope": "projects:read",
            },
        },
    )
    result = CliRunner().invoke(app, ["doctor", "--json"])
    checks = {c["name"]: c for c in json.loads(result.stdout)["checks"]}
    assert checks["scopes"]["status"] == "fail"
    assert "projects:read" in checks["scopes"]["detail"]
    assert "beakr auth login" in checks["scopes"]["detail"]


@respx.mock
def test_logout_does_not_ask_beakr_to_revoke_a_clerk_token(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    _save_clerk_tokens()
    revoke = respx.post(f"{API}/v1/auth/token/revoke").respond(204)
    result = CliRunner().invoke(app, ["auth", "logout"])
    assert result.exit_code == 0, result.output
    assert not revoke.called
    assert TokenStore().load() is None


# -- engine follow-ups with an API key -------------------------------------------


@pytest.fixture
def keyed(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


@pytest.fixture
def rendered(monkeypatch):
    """Render tables and text as a terminal would; CliRunner output is piped."""
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)


@respx.mock
def test_conversations_show_explains_a_skipped_compound(keyed, rendered):
    respx.get(f"{API}/v1/conversations/c1").respond(
        json={
            "id": "c1",
            "title": "Importer work",
            "client": "codex",
            "session_id": "s1",
            "message_count": 4,
            "compounded_through": 0,
            "compound_status": "skipped",
            "last_compound_result": {"status": "skipped", "reason": "insufficient_content"},
        }
    )
    result = CliRunner().invoke(app, ["conversations", "show", "c1"])
    assert result.exit_code == 0, result.output
    text = " ".join(result.output.split())
    assert "0/4" in text and "skipped" in text and "insufficient_content" in text


@respx.mock
def test_conversations_ls_shows_partial_progress(keyed, rendered, monkeypatch):
    monkeypatch.setenv("COLUMNS", "200")
    respx.get(f"{API}/v1/conversations").respond(
        json={
            "data": [
                {
                    "id": "c1",
                    "client": "claude-code",
                    "title": "Big session",
                    "message_count": 300,
                    "compounded_through": 120,
                    "compound_status": "partial",
                }
            ],
            "has_more": False,
        }
    )
    result = CliRunner().invoke(app, ["conversations", "ls"])
    assert result.exit_code == 0, result.output
    assert "partial 120/300" in result.output


# -- MCP ------------------------------------------------------------------------


@respx.mock
async def test_mcp_audit_log_uses_the_org_override(monkeypatch):
    from beakr_cli.mcp_tools_platform import org_audit_log

    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")
    monkeypatch.setenv("BEAKR_ORG_ID", "org-9")
    route = respx.get(f"{API}/v1/organizations/org-9/audit").respond(
        json={
            "data": [
                {
                    "created_at": "2026-10-09T01:02:03",
                    "action": "auth.policy_updated",
                    "actor_user_id": "u1",
                }
            ],
            "next_cursor": "c1",
            "has_more": True,
        }
    )
    result = await org_audit_log(limit=500)
    text = result["content"]
    assert "auth.policy_updated by u1" in text
    # Older events are one cursor away (SPEC-488): the route's cursor, not a timestamp.
    assert 'cursor="c1"' in text and result["coverage"]["next_cursor"] == "c1"
    assert route.calls[0].request.url.params["limit"] == "50"


@respx.mock
async def test_mcp_conversation_search_flags_unfinished_compounds(monkeypatch):
    from beakr_cli.mcp_tools_platform import conversations_search

    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")
    # A query goes through the conversation index (GET /v1/conversations/search).
    respx.get(f"{API}/v1/conversations/search").respond(
        json={"data": [{"id": "c1", "title": "T", "kind": "imported", "compound_status": "failed"}]}
    )
    assert "compound: failed" in (await conversations_search(query="t"))["content"]
    # Without a query it lists recent sessions, also flagging unfinished compounds.
    respx.get(f"{API}/v1/conversations").respond(
        json={"data": [{"id": "c2", "title": "U", "client": "codex", "compound_status": "partial"}]}
    )
    assert "compound: partial" in (await conversations_search())["content"]
