"""Whole JSON bodies must satisfy required boolean fields without duplicate flags.

The learning-settings operations are unlisted (reachable, not in the documented
contract), so ``beakr api`` no longer builds them; the command ``beakr api``
would build is constructed from their shape, written out below as the engine
serves it, and run directly.
"""

import json

import click.testing
import pytest
import respx

from beakr_cli.api_manifest import build_manifest
from beakr_cli.commands.api import build_command


def _learning_switch(scope: str) -> dict:
    return {
        "put": {
            "operationId": f"set_learning_{scope.replace(':', '_')}",
            "summary": "Turn learning on or off",
            "tags": ["auth"],
            "x-beakr-scope": scope,
            "security": [{"bearer": []}],
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/LearningEnabledUpdate"}
                    }
                },
            },
            "responses": {
                "200": {
                    "description": "The setting.",
                    "content": {"application/json": {"schema": {"type": "object"}}},
                }
            },
        }
    }


_CONTRACT = {
    "openapi": "3.1.0",
    "info": {"title": "learning switches", "version": "1"},
    "paths": {
        "/v1/me/learning": _learning_switch("profiles:write"),
        "/v1/me/learning/org": _learning_switch("members:admin"),
    },
    "components": {
        "schemas": {
            "LearningEnabledUpdate": {
                "type": "object",
                "required": ["enabled"],
                "properties": {
                    "enabled": {
                        "type": "boolean",
                        "description": "Whether personal learning is allowed",
                    }
                },
            }
        }
    },
}


def _command(op: str) -> click.Command:
    spec = _CONTRACT
    entry = next(e for e in build_manifest(spec)["operations"] if e["op"] == op)
    return build_command(entry)


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("org", [False, True])
def test_whole_json_satisfies_required_boolean(monkeypatch, enabled, org):
    monkeypatch.setenv("BEAKR_BASE_URL", "https://api.test")
    monkeypatch.setenv("BEAKR_API_KEY", "bk_test_fixture")
    path = "/v1/me/learning/org" if org else "/v1/me/learning"
    body = {"enabled": enabled}
    with respx.mock(assert_all_called=False) as router:
        route = router.put("https://api.test" + path).respond(200, json=body)
        result = click.testing.CliRunner().invoke(
            _command(f"PUT {path}"), ["--data", json.dumps(body)]
        )
    assert result.exit_code == 0, result.output
    assert route.call_count == 1
    assert json.loads(route.calls[0].request.content) == body
