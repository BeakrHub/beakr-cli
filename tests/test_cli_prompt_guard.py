"""No prompts when stdin is not a TTY (spec W7 item 2).

Root cause this guards: ``kb proposals accept|dismiss``, ``kb undo-run``, the
``--accept`` step of a created proposal and the ``beakr init`` project picker
called ``typer.confirm``/``typer.prompt`` directly. With stdin piped from a
script or agent, the prompt either blocked forever or consumed whatever was
piped in as its answer. Every question now goes through ``beakr_cli.prompts``,
which refuses with exit 2 and names the flag to pass instead; the static gate
below fails on a direct prompt call anywhere else.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.main import internal_app as app

API = "https://api.test"
SRC = Path(__file__).resolve().parents[1] / "src"
#: The one module allowed to ask: it checks for a TTY first.
PROMPT_MODULE = SRC / "beakr_cli" / "prompts.py"

#: ``<module>.<function>`` calls that read an answer from stdin.
_PROMPT_CALLS = {
    ("typer", "confirm"),
    ("typer", "prompt"),
    ("click", "confirm"),
    ("click", "prompt"),
    ("Confirm", "ask"),
    ("Prompt", "ask"),
    ("IntPrompt", "ask"),
    ("getpass", "getpass"),
}
_PROMPT_NAMES = {"input", "getpass"}
_PROMPT_IMPORTS = {
    ("typer", "confirm"),
    ("typer", "prompt"),
    ("click", "confirm"),
    ("click", "prompt"),
    ("getpass", "getpass"),
}

runner = CliRunner()


def _prompt_sites(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    sites: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                if (node.module, alias.name) in _PROMPT_IMPORTS:
                    sites.append(f"{path}:{node.lineno} imports {node.module}.{alias.name}")
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and (func.value.id, func.attr) in _PROMPT_CALLS
        ):
            sites.append(f"{path}:{node.lineno} calls {func.value.id}.{func.attr}")
        elif isinstance(func, ast.Name) and func.id in _PROMPT_NAMES:
            sites.append(f"{path}:{node.lineno} calls {func.id}()")
    return sites


def test_no_prompt_is_asked_outside_the_guarded_helpers():
    sources = [
        path
        for package in ("beakr_cli", "beakr_sdk")
        for path in sorted((SRC / package).rglob("*.py"))
        if path != PROMPT_MODULE
    ]
    assert sources, "found no source files to scan"
    offenders = [site for path in sources for site in _prompt_sites(path)]
    assert offenders == [], "prompt outside beakr_cli.prompts:\n" + "\n".join(offenders)


def test_the_gate_recognises_a_direct_prompt(tmp_path):
    # Guards the gate itself: an AST shape it stopped matching would pass silently.
    sample = tmp_path / "sample.py"
    sample.write_text(
        "import typer\nfrom click import prompt\n"
        "def f():\n    typer.confirm('x')\n    input('y')\n",
        encoding="utf-8",
    )
    assert len(_prompt_sites(sample)) == 3


@pytest.fixture
def authenticated(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


@pytest.mark.parametrize(
    "args",
    [
        ["kb", "proposals", "accept", "prop-1"],
        ["kb", "proposals", "dismiss", "prop-1"],
        ["kb", "undo-run", "run-7"],
        ["conversations", "rm", "c-1"],
        ["memory", "rm", "m-1"],
    ],
)
def test_destructive_commands_refuse_without_yes_when_stdin_is_not_a_tty(authenticated, args):
    with respx.mock(assert_all_called=False) as router:
        result = runner.invoke(app, args, input="y\n")
        # Refused before any request: a piped "y" must not be read as consent.
        assert not router.calls
    assert result.exit_code == 2, result.output
    assert "--yes" in result.stderr


def test_init_without_a_project_refuses_before_listing_projects(authenticated):
    with respx.mock(assert_all_called=False) as router:
        result = runner.invoke(app, ["init"], input="1\n")
        assert not router.calls
    assert result.exit_code == 2
    assert "--project or --create" in result.stderr


def test_auth_set_scope_without_a_project_refuses_when_not_interactive(authenticated):
    with respx.mock(assert_all_called=False) as router:
        result = runner.invoke(app, ["auth", "set-scope"], input="1\n")
        assert not router.calls
    assert result.exit_code == 2
    assert "--project" in result.stderr


def _proposal_routes():
    created = respx.post(f"{API}/v1/knowledge/wiki/proposals").respond(
        json={"id": "prop-1", "status": "pending", "proposal_type": "create"}
    )
    accepted = respx.post(f"{API}/v1/knowledge/wiki/proposals/prop-1/accept").respond(
        json={"proposal_id": "prop-1", "status": "accepted"}
    )
    return created, accepted


_PROPOSE = [
    "kb",
    "propose",
    "create",
    "--title",
    "T",
    "--content",
    "Body",
    "--project",
    "22222222-2222-2222-2222-222222222222",
    "--accept",
]


@respx.mock
def test_created_proposal_accept_refuses_without_yes_when_stdin_is_not_a_tty(
    authenticated, monkeypatch
):
    # Terminal stdout, piped stdin: the human-output path that used to prompt.
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)
    created, accepted = _proposal_routes()
    result = runner.invoke(app, _PROPOSE, input="y\n")
    assert created.called
    assert not accepted.called
    assert result.exit_code == 2
    assert "--yes" in result.stderr


@respx.mock
@pytest.mark.parametrize(("answer", "accepts"), [("y\n", True), ("n\n", False)])
def test_created_proposal_accept_asks_on_a_tty(authenticated, monkeypatch, answer, accepts):
    monkeypatch.setattr("beakr_cli.output.is_interactive", lambda: True)
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)
    _, accepted = _proposal_routes()
    result = runner.invoke(app, _PROPOSE, input=answer)
    # Declining leaves the proposal pending; that is an answer, not a failure.
    assert result.exit_code == 0, result.output
    assert accepted.called is accepts


@respx.mock
def test_declining_on_a_tty_exits_1_without_acting(authenticated, monkeypatch):
    monkeypatch.setattr("beakr_cli.output.is_interactive", lambda: True)
    dismissed = respx.post(f"{API}/v1/knowledge/wiki/proposals/prop-1/dismiss").respond(
        json={"proposal_id": "prop-1", "status": "dismissed"}
    )
    result = runner.invoke(app, ["kb", "proposals", "dismiss", "prop-1"], input="n\n")
    assert result.exit_code == 1
    assert not dismissed.called
