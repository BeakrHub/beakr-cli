"""The stdio ``context`` and ``beakr_version`` say where their calls land.

Root cause (agent tester report, 2026-11-13): nothing either MCP server
returned named the deployment or the organization a connection had reached, so
an agent on sandbox, or in the wrong organization, read an empty Knowledge as
"nothing is known". The hosted server now leads ``context`` and
``beakr_version`` with ``Connected to: Beakr <environment>, organization
"<name>" (slug <slug>).``; the stdio server printed neither. It prints the same
sentence, built by a copy of the engine's ``workspace_line``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import respx
from httpx import Response

from beakr_cli import mcp_server, mcp_tools_platform, mcp_workspace, updates
from tests._engine import engine_derived, refresh_snapshots

API = "https://api.example.com"
WORKSPACE = {"environment": "sandbox", "organization": {"name": "Acme Labs", "slug": "acme"}}
LINE = 'Connected to: Beakr sandbox, organization "Acme Labs" (slug acme).'
REFUSED = Response(403, json={"error": {"code": "forbidden", "message": "No."}})

_MIRROR = Path(__file__).resolve().parents[1] / "src" / "beakr_cli" / "mcp_workspace.py"


@pytest.fixture(autouse=True)
def api(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "test-token")


def test_the_copy_matches_the_engine():
    # The engine's file, else (in CI) the snapshot's copy of just this function.
    snapshots = refresh_snapshots()
    expected = engine_derived(
        snapshots.WORKSPACE_IDENTITY, snapshots.WORKSPACE_LINE, snapshots.workspace_line
    ).rstrip()
    mirror = _MIRROR.read_text()
    assert mirror[mirror.index("def workspace_line(") :].rstrip() == expected, (
        "beakr_cli/mcp_workspace.py differs from the engine's workspace_line: copy it again"
    )


# -- context ---------------------------------------------------------------------


@respx.mock
async def test_context_leads_with_where_it_landed():
    respx.post(f"{API}/v1/context").mock(
        return_value=Response(200, json={"content": "ctx", "workspace": WORKSPACE})
    )

    text = await mcp_tools_platform.context()

    assert text == f"{LINE}\n\nctx"


@respx.mock
async def test_context_names_the_environment_alone_when_the_organization_is_unreadable():
    respx.post(f"{API}/v1/context").mock(
        return_value=Response(
            200,
            json={"content": "ctx", "workspace": {"environment": "production"}},
        )
    )

    assert await mcp_tools_platform.context() == "Connected to: Beakr production.\n\nctx"


@respx.mock
@pytest.mark.parametrize("fmt", ["claude_md", "agents_md", "json"])
async def test_a_context_written_to_a_file_or_parsed_carries_no_line(fmt):
    # A managed file (CLAUDE.md, AGENTS.md) is written to disk as returned.
    respx.post(f"{API}/v1/context").mock(
        return_value=Response(200, json={"content": "ctx", "workspace": WORKSPACE})
    )

    assert await mcp_tools_platform.context(format=fmt) == "ctx"


@respx.mock
async def test_context_is_unchanged_for_an_api_that_does_not_say():
    respx.post(f"{API}/v1/context").mock(return_value=Response(200, json={"content": "ctx"}))

    assert await mcp_tools_platform.context() == "ctx"


# -- beakr_version ---------------------------------------------------------------


@pytest.fixture
def installed(monkeypatch):
    """A uv install that is up to date, with PyPI never asked."""
    info = updates.InstallInfo(updates.InstallMethod.other, None, "pip install --upgrade")
    monkeypatch.setattr(mcp_server, "detect_install", lambda: info)
    monkeypatch.setattr(
        mcp_server,
        "get_update_status",
        lambda **_: updates.UpdateStatus("0.3.0", "0.3.0", None),
    )


def _organization(response: Response) -> respx.Route:
    return respx.get(f"{API}/v1/me/organization").mock(return_value=response)


def _context(response: Response) -> respx.Route:
    return respx.post(f"{API}/v1/context").mock(return_value=response)


@respx.mock
async def test_beakr_version_says_which_environment_and_organization_answered(installed):
    # The organization from the route that needs no scope, as the hosted tool
    # reads it; the environment from the context route's ``workspace``.
    organization = _organization(
        Response(200, json={"id": "o1", "name": "Acme Labs", "slug": "acme"})
    )
    context = _context(
        Response(200, json={"content": "ctx", "workspace": {"environment": "sandbox"}})
    )

    lines = (await mcp_server.beakr_version()).splitlines()

    assert lines == [
        "Installed: beakr-cli 0.3.0 (other)",
        LINE,
        "No newer release (latest release: 0.3.0).",
    ]
    assert organization.called
    # The smallest context the route builds: only its workspace is read.
    assert json.loads(context.calls.last.request.content) == {
        "format": "json",
        "budget_chars": 2000,
    }


@respx.mock
async def test_beakr_version_names_what_it_could_read(installed):
    _organization(Response(200, json={"id": "o1", "name": "Acme Labs", "slug": "acme"}))
    _context(REFUSED)
    assert 'Connected to: Beakr, organization "Acme Labs" (slug acme).' in (
        await mcp_server.beakr_version()
    )

    _organization(REFUSED)
    _context(Response(200, json={"content": "", "workspace": {"environment": "production"}}))
    assert "Connected to: Beakr production." in await mcp_server.beakr_version()


@respx.mock
async def test_beakr_version_still_answers_when_nothing_says_where_it_landed(installed):
    _organization(REFUSED)
    _context(Response(500, json={"detail": "boom"}))

    assert await mcp_server.beakr_version() == (
        "Installed: beakr-cli 0.3.0 (other)\nNo newer release (latest release: 0.3.0)."
    )


async def test_beakr_version_still_answers_with_no_api_to_ask(installed, monkeypatch):
    async def unreachable(request):
        raise RuntimeError("no credential")

    monkeypatch.setattr(mcp_workspace, "call_api", unreachable)

    assert "Connected to" not in await mcp_server.beakr_version()


@respx.mock
async def test_a_self_hosted_installation_is_named_too(monkeypatch):
    info = updates.InstallInfo(updates.InstallMethod.other, None, "pip install --upgrade")
    monkeypatch.setattr(mcp_server, "detect_install", lambda: info)
    monkeypatch.setattr(updates, "server_deployment", lambda: "self_hosted")
    monkeypatch.setenv(updates.DISABLE_ENV, "1")
    _organization(Response(200, json={"name": "Acme Labs", "slug": "acme"}))
    _context(Response(200, json={"workspace": {"environment": "production"}}))

    lines = (await mcp_server.beakr_version()).splitlines()

    assert lines[0].startswith(f"Installed: beakr-cli {mcp_server.__version__}, bundled with")
    assert lines[1:] == ['Connected to: Beakr production, organization "Acme Labs" (slug acme).']


async def test_beakr_version_does_not_wait_on_an_api_that_does_not_answer(installed, monkeypatch):
    import asyncio

    async def silent(request):
        await asyncio.sleep(60)

    monkeypatch.setattr(mcp_workspace, "call_api", silent)
    monkeypatch.setattr(mcp_workspace, "_READ_SECONDS", 0.01)

    assert await mcp_server.beakr_version() == (
        "Installed: beakr-cli 0.3.0 (other)\nNo newer release (latest release: 0.3.0)."
    )
