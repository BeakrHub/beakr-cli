"""The ``beakr`` entry point registers only the documented core (2026-10-12).

Root cause: the CLI, like the API and SDKs, exposed every reachable route (180
commands), so a newcomer met profile, skills, webhooks, workflows and org-admin
commands over routes the published contract no longer documents. The entry
point (``beakr_cli.main.app``) is now :data:`beakr_cli.command_surface.CORE_COMMANDS`
cut from the full tree (``beakr_cli.main.internal_app``); every other command
module stays, registered in the full tree only, so its tests keep running.

Also pins the commands added for the new core operations: integration tools,
interview links and end-user tokens.
"""

from __future__ import annotations

import json
import re

import click
import pytest
import respx
import typer
from typer.testing import CliRunner

from beakr_cli.command_surface import CORE_COMMANDS
from beakr_cli.main import app, internal_app

API = "https://api.test"
runner = CliRunner()


@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


def _tree(root: typer.Typer) -> set[str]:
    found: set[str] = set()

    def walk(command: click.Command, path: list[str]) -> None:
        if isinstance(command, click.Group):
            for name, sub in command.commands.items():
                walk(sub, [*path, name])
        else:
            found.add(" ".join(path))

    walk(typer.main.get_command(root), [])
    return found


def test_the_entry_point_is_exactly_the_core_commands():
    assert _tree(app) == set(CORE_COMMANDS)


def test_every_core_command_is_a_real_command():
    assert set(CORE_COMMANDS) <= _tree(internal_app)


def test_unlisted_command_modules_stay_in_the_full_tree_only():
    full, public = _tree(internal_app), _tree(app)
    for command in [
        "profile show",
        "memory ls",
        "skills sync",
        "workflows run",
        "outputs ls",
        "usage",
        "members invite",
        "files versions",
        "integrations sync",
        "kb blame",
        "kb undo-run",
        "interviews request",
    ]:
        assert command in full, command
        assert command not in public, command


def test_help_lists_only_core_groups():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    listed = set(re.findall(r"^\W+([a-z][a-z-]*) {2,}", result.output, flags=re.MULTILINE))
    assert {"kb", "projects", "integrations", "end-user-tokens"} <= listed
    for group in ("profile", "skills", "workflows", "outputs", "usage"):
        assert group not in listed, group


def test_an_unlisted_command_is_not_found_on_the_entry_point():
    result = runner.invoke(app, ["workflows", "ls"])
    assert result.exit_code == 2
    assert "No such command" in result.output


def test_the_full_tree_is_left_whole_by_the_cut():
    # The published tree is a copy: building it must not drop commands from the
    # shared sub-apps (kb, org, projects ...) the full tree is built from.
    assert {"kb blame", "org policy show", "files versions"} <= _tree(internal_app)
    assert len(_tree(internal_app)) > len(_tree(app))


# -- the commands over the new core operations ----------------------------------------


def _body(route) -> dict:
    return json.loads(route.calls.last.request.content)


@respx.mock
def test_integrations_tools_lists_the_callers_tools():
    route = respx.get(f"{API}/v1/integrations/tools").respond(
        200,
        json={
            "data": [
                {
                    "name": "slack_search",
                    "provider": "slack",
                    "description": "Search Slack",
                    "connections": [{"connector_id": "c1"}],
                    "input_schema": {},
                }
            ],
            "next_cursor": None,
            "has_more": False,
        },
    )
    result = runner.invoke(app, ["integrations", "tools", "--provider", "slack", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)[0]["name"] == "slack_search"
    assert route.calls.last.request.url.params["provider"] == "slack"


@respx.mock
def test_integrations_call_sends_the_arguments():
    route = respx.post(f"{API}/v1/integrations/tools/slack_search/call").respond(
        200,
        json={
            "tool": "slack_search",
            "provider": "slack",
            "connector_ids": ["c1"],
            "content": "assay handover notes",
            "is_error": False,
            "sources": [],
        },
    )
    result = runner.invoke(
        app,
        [
            "integrations",
            "call",
            "slack_search",
            "--args",
            '{"query": "assay"}',
            "--arg",
            "limit=5",
            "--arg",
            "channel=general",
            "--connector",
            "c1",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert _body(route) == {
        "arguments": {"query": "assay", "limit": 5, "channel": "general"},
        "connector_id": "c1",
    }


@respx.mock
def test_integrations_call_exits_1_when_the_tool_reports_an_error():
    respx.post(f"{API}/v1/integrations/tools/slack_search/call").respond(
        200, json={"tool": "slack_search", "content": "rate limited", "is_error": True}
    )
    result = runner.invoke(app, ["integrations", "call", "slack_search", "--json"])
    assert result.exit_code == 1


def test_integrations_call_refuses_a_malformed_argument():
    result = runner.invoke(app, ["integrations", "call", "slack_search", "--arg", "nokey"])
    assert result.exit_code == 2


@respx.mock
def test_interviews_link_create_show_send_and_revoke():
    create = respx.post(f"{API}/v1/interview/links").respond(
        201,
        json={
            "link_id": "l1",
            "sendable_id": "s1",
            "url": "https://app.test/i/secret",
            "expires_at": "2026-10-26T00:00:00Z",
            "result_url": "/v1/interview/sendables/links/l1/result",
        },
    )
    result = runner.invoke(
        app,
        [
            "interviews",
            "link",
            "create",
            "Assay handover",
            "--goal",
            "How the binding assay is run",
            "-q",
            "Controls?",
            "--name",
            "Ada",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "https://app.test/i/secret" in result.output
    assert _body(create) == {
        "title": "Assay handover",
        "goal": "How the binding assay is run",
        "questions": ["Controls?"],
        "recipient": {"name": "Ada"},
    }

    respx.get(f"{API}/v1/interview/sendables/links/l1/result").respond(
        200,
        json={
            "link_id": "l1",
            "status": "completed",
            "write_up": "They run controls first.",
            "completed_at": "2026-10-13T00:00:00Z",
        },
    )
    shown = runner.invoke(app, ["interviews", "link", "show", "l1"])
    assert shown.exit_code == 0, shown.output
    assert "They run controls first." in shown.output

    send = respx.post(f"{API}/v1/interview/sendables/links/l1/knowledge").respond(
        200, json=[{"project_id": "p1", "status": "queued"}]
    )
    sent = runner.invoke(app, ["interviews", "link", "send", "l1", "-P", "p1", "-P", "p2"])
    assert sent.exit_code == 0, sent.output
    assert _body(send) == {"project_ids": ["p1", "p2"]}

    revoke = respx.post(f"{API}/v1/interview/sendables/links/l1/revoke").respond(
        200, json={"ok": True}
    )
    assert runner.invoke(app, ["interviews", "link", "revoke", "l1"]).exit_code == 0
    assert revoke.called


def test_interviews_link_create_needs_a_goal_or_a_question():
    result = runner.invoke(app, ["interviews", "link", "create", "Assay handover"])
    assert result.exit_code == 2


@respx.mock
def test_interviews_link_ls_lists_sendables():
    respx.get(f"{API}/v1/interview/sendables").respond(
        200,
        json={
            "data": [{"id": "s1", "title": "Assay handover", "links": [{"id": "l1"}]}],
            "next_cursor": None,
            "has_more": False,
        },
    )
    result = runner.invoke(app, ["interviews", "link", "ls", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)[0]["id"] == "s1"


@respx.mock
def test_end_user_tokens_mint_ls_revoke():
    mint = respx.post(f"{API}/v1/auth/end-user-tokens").respond(
        201,
        json={
            "token": "bk_at_secret",
            "token_id": "t1",
            "expires_at": "2026-10-12T15:00:00Z",
            "subject": "customer-42",
            "scopes": ["kb:read"],
            "project_ids": ["p1"],
        },
    )
    result = runner.invoke(
        app,
        [
            "end-user-tokens",
            "mint",
            "--subject",
            "customer-42",
            "-P",
            "p1",
            "-s",
            "kb:read",
            "--ttl",
            "600",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "bk_at_secret" in result.output
    assert _body(mint) == {
        "subject": "customer-42",
        "project_ids": ["p1"],
        "scopes": ["kb:read"],
        "ttl_seconds": 600,
    }

    listed = respx.get(f"{API}/v1/auth/end-user-tokens").respond(
        200,
        json={
            "data": [{"token_id": "t1", "subject": "customer-42", "scopes": ["kb:read"]}],
            "next_cursor": None,
            "has_more": False,
        },
    )
    shown = runner.invoke(app, ["end-user-tokens", "ls", "--subject", "customer-42", "--json"])
    assert shown.exit_code == 0, shown.output
    assert json.loads(shown.output)[0]["token_id"] == "t1"
    assert listed.calls.last.request.url.params["subject"] == "customer-42"

    revoke = respx.delete(f"{API}/v1/auth/end-user-tokens/t1").respond(204)
    assert runner.invoke(app, ["end-user-tokens", "revoke", "t1"]).exit_code == 0
    assert revoke.called


@respx.mock
def test_end_user_tokens_mint_for_an_external_user():
    mint = respx.post(f"{API}/v1/auth/end-user-tokens").respond(201, json={"token_id": "t2"})
    result = runner.invoke(
        app,
        [
            "end-user-tokens",
            "mint",
            "--external-user",
            "5b1f6c2e-8d3a-4e7b-9c1d-2a3b4c5d6e7f",
            "-s",
            "kb:read",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert _body(mint) == {
        "external_user_id": "5b1f6c2e-8d3a-4e7b-9c1d-2a3b4c5d6e7f",
        "scopes": ["kb:read"],
    }


@pytest.mark.parametrize(
    "argv",
    [
        ["end-user-tokens", "mint", "-s", "kb:read"],
        ["end-user-tokens", "mint", "-s", "kb:read", "--subject", "x"],
        ["end-user-tokens", "mint", "-s", "kb:read", "--subject", "x", "--external-user", "u"],
    ],
)
def test_end_user_tokens_mint_needs_a_subject_with_projects_or_an_external_user(argv):
    assert runner.invoke(app, argv).exit_code == 2
