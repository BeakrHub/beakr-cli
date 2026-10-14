"""Codex's AGENTS.md capture notice stays correct as consent and bindings change (SPEC-480).

Root cause (audit C092): ``refresh_codex_context`` wrote the engine's context into
the AGENTS.md block verbatim. Claude's SessionStart hook appends a line saying
whether the session is captured; Codex's block never did, and nothing rewrote
the block when consent was withdrawn, the repository was rebound, or the engine
could not be reached. The notice is now computed from local consent on every
write, and consent or binding changes rewrite it without the engine.
"""

from __future__ import annotations

import json
from pathlib import Path

import respx
from typer.testing import CliRunner

from beakr_cli import agent_hooks
from beakr_cli.codex_context import read_block
from beakr_cli.commands.codex_wrapper import refresh_codex_context
from beakr_cli.main import app
from beakr_cli.project_binding import write_binding

API = "https://api.test"
PROJECTS = {
    "data": [
        {"id": "p1", "name": "Assays", "org_id": "o1"},
        {"id": "p2", "name": "Pilot", "org_id": "o1"},
    ],
    "next_cursor": None,
    "has_more": False,
}


def _repo(tmp_path: Path, monkeypatch, *, capture: bool, name: str = "repo") -> Path:
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_test_notice")
    repo = tmp_path / name
    repo.mkdir()
    write_binding(
        repo,
        {"project_id": "p1", "name": "Assays", "base_url": API, "org_id": None, "capture": capture},
    )
    monkeypatch.chdir(repo)
    return repo


def _refresh(repo: Path, *, status: int = 200, content: str = "# Assays\nP1 FACTS") -> bool:
    with respx.mock(assert_all_called=False) as mock:
        mock.post(f"{API}/v1/context").respond(status, json={"content": content})
        return refresh_codex_context(repo)


def _agents() -> str:
    return agent_hooks.codex_agents_path().read_text()


def _notice() -> str:
    text = _agents()
    start = text.index("<!-- beakr:capture-notice:start -->")
    end = text.index("<!-- beakr:capture-notice:end -->")
    return text[start:end].lower()


def test_withdrawing_consent_rewrites_the_notice_and_keeps_the_facts(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch, capture=True)
    assert _refresh(repo)
    assert "capture is on" in _notice() and str(repo.resolve()).lower() in _notice()

    result = CliRunner().invoke(app, ["hooks", "capture", "off"])

    assert result.exit_code == 0, result.output
    assert "capture is off" in _notice() and "capture is on" not in _notice()
    assert "beakr hooks capture on" in _notice()
    assert "P1 FACTS" in _agents()
    assert _agents().count(agent_hooks.AGENTS_START) == 1

    CliRunner().invoke(app, ["hooks", "capture", "on"])
    assert "capture is on" in _notice()


def test_a_consent_change_in_another_repository_leaves_this_notice_alone(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch, capture=True)
    assert _refresh(repo)
    other = tmp_path / "other"
    other.mkdir()
    write_binding(other, {"project_id": "p2", "base_url": API, "org_id": None, "capture": True})
    monkeypatch.chdir(other)

    CliRunner().invoke(app, ["hooks", "capture", "off"])

    # The block describes `repo`, which still consents.
    assert "capture is on" in _notice() and str(repo.resolve()).lower() in _notice()


def test_rebinding_to_another_project_drops_its_facts_and_resets_the_notice(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch, capture=True)
    assert _refresh(repo)

    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{API}/v1/projects").respond(json=PROJECTS)
        mock.get(f"{API}/v1/organizations/me/active").respond(json={"id": "o1"})
        result = CliRunner().invoke(app, ["init", "--project", "p2", "--json"])

    assert result.exit_code == 0, result.output
    # Rebinding resets consent, and P1's facts no longer describe this repository.
    assert "capture is off" in _notice() and "pilot" in _notice()
    assert "P1 FACTS" not in _agents()
    assert read_block().project_id == "p2"


def test_a_failed_refresh_rewrites_the_notice_from_local_consent(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch, capture=True)
    assert _refresh(repo)
    # Consent withdrawn by hand (no CLI command ran to rewrite the block).
    write_binding(repo, {"project_id": "p1", "name": "Assays", "base_url": API, "org_id": None})

    assert not _refresh(repo, status=500)

    assert "capture is off" in _notice() and "capture is on" not in _notice()
    # Same directory, same project: the last facts are still the right ones.
    assert "P1 FACTS" in _agents()


def test_a_failed_refresh_after_a_project_switch_never_keeps_the_old_facts(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch, capture=False)
    assert _refresh(repo)
    write_binding(
        repo,
        {"project_id": "p2", "name": "Pilot", "base_url": API, "org_id": None, "capture": True},
    )

    assert not _refresh(repo, status=500)

    assert "P1 FACTS" not in _agents()
    assert "could not be loaded" in _agents()
    assert "capture is on" in _notice() and "pilot" in _notice()


def test_a_failed_refresh_in_another_repository_describes_that_repository(tmp_path, monkeypatch):
    first = _repo(tmp_path, monkeypatch, capture=True, name="first")
    assert _refresh(first)
    second = tmp_path / "second"
    second.mkdir()
    monkeypatch.chdir(second)

    assert not _refresh(second, status=500)

    assert "P1 FACTS" not in _agents()
    notice = _notice()
    assert "capture is off" in notice and "not bound" in notice
    assert str(second.resolve()).lower() in notice


def test_a_failed_first_refresh_still_writes_a_notice(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch, capture=True)
    assert not agent_hooks.agents_block_installed()

    assert not _refresh(repo, status=503)

    assert "capture is on" in _notice()


def test_hooks_install_for_codex_includes_the_notice_and_the_bound_project(tmp_path, monkeypatch):
    _repo(tmp_path, monkeypatch, capture=False)
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(f"{API}/v1/context").respond(json={"content": "P1 FACTS"})
        result = CliRunner().invoke(app, ["hooks", "install", "--client", "codex"])

    assert result.exit_code == 0, result.output
    assert json.loads(route.calls[0].request.content)["project_id"] == "p1"
    assert "capture is off" in _notice()
    CliRunner().invoke(app, ["hooks", "uninstall", "--client", "codex"])
    assert not agent_hooks.agents_block_installed()


def test_a_block_without_a_notice_is_left_for_the_next_refresh(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch, capture=True)
    agent_hooks.write_agents_block("Legacy context")

    CliRunner().invoke(app, ["hooks", "capture", "off"])

    assert "Legacy context" in _agents()
    assert _refresh(repo)
    assert "capture is off" in _notice()
