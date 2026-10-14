"""The investor-update skill recipe runs as published, against the real SDK.

``docs/developers/recipes/investor-update-skill.md`` in beakr-engine had no
test binding its code, so a renamed SDK argument or a changed route would have
left it describing calls that fail. These tests execute every Python block of
the recipe, in order, against a mocked API and check what each one sends.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
import pytest
import respx

API = "https://api.test"
RECIPE = (
    Path(__file__).resolve().parents[2]
    / "beakr-engine"
    / "docs"
    / "developers"
    / "recipes"
    / "investor-update-skill.md"
)
pytestmark = pytest.mark.skipif(
    not RECIPE.exists(), reason="beakr-engine checkout not beside beakr-cli"
)


def _blocks(language: str) -> list[str]:
    return re.findall(rf"```{language}\n(.*?)```", RECIPE.read_text(), flags=re.DOTALL)


@pytest.fixture
def recipe(monkeypatch, capsys):
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")
    monkeypatch.setenv("BEAKR_BASE_URL", API)
    with respx.mock:
        routes = {
            "create": respx.post(f"{API}/v1/me/skills").mock(
                return_value=httpx.Response(
                    201,
                    json={"id": "skill-1", "name": "investor-update", "visibility": "org"},
                )
            ),
            "command": respx.post(f"{API}/v1/knowledge/wiki/command").mock(
                return_value=httpx.Response(
                    200,
                    json={
                        "content": [{"type": "text", "text": "3 row(s) from 3 card(s)"}],
                        "outcome": {"status": "ok"},
                        "coverage": {
                            "command": "rows",
                            "returned": 3,
                            "total": 3,
                            "complete": True,
                        },
                    },
                )
            ),
        }
        namespace: dict = {}
        for block in _blocks("python"):
            # Runs the recipe exactly as published (repo-controlled doc), not user input.
            exec(compile(block, str(RECIPE), "exec"), namespace)  # noqa: S102
        yield namespace, routes, capsys.readouterr().out


def test_the_skill_is_created_with_its_instructions_and_visibility(recipe) -> None:
    namespace, routes, out = recipe
    body = json.loads(routes["create"].calls[0].request.content)
    assert body["name"] == "investor-update"
    assert body["knowledge"].lstrip().startswith("# Investor update")
    assert "never estimate" in body["knowledge"]
    assert body["description"] == "How we draft the monthly investor update"
    assert body["triggers"] == "investor update, monthly update, board update"
    assert body["visibility"] == "org"
    assert namespace["skill"]["id"] == "skill-1"
    assert "skill-1" in out


def test_the_numbers_come_from_current_metric_facts(recipe) -> None:
    _namespace, routes, out = recipe
    body = json.loads(routes["command"].calls[0].request.content)
    assert body == {
        "command": "rows",
        "table": "facts",
        "page_type": "metric",
        "current_only": True,
        "limit": 500,
    }
    assert "3 row(s) from 3 card(s)" in out


def test_the_shell_steps_name_commands_the_cli_has() -> None:
    from typer.main import get_command

    from beakr_cli.main import app

    cli = get_command(app)
    for block in _blocks("sh"):
        for line in block.splitlines():
            words = line.split("#", 1)[0].split()
            if not words:
                continue
            assert words[0] == "beakr"
            group = cli.commands[words[1]]
            assert words[2] in group.commands, line
