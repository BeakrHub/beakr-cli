"""``beakr kb rows|add-fact|add-connection|add-finding|edit-rows`` send door bodies.

The door's argument names are the in-product tools' names; a CLI that renamed
one (``start`` for ``start_date``) would be silently ignored by the server.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.main import internal_app as app

API = "https://api.test"


@pytest.fixture(autouse=True)
def api_env(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


def _ok(text: str = "ok") -> httpx.Response:
    return httpx.Response(
        200,
        json={"content": [{"type": "text", "text": text}], "outcome": {"status": "success"}},
    )


def _body(route) -> dict:
    return json.loads(route.calls.last.request.content)


@respx.mock
def test_rows_sends_the_rows_command_with_door_argument_names() -> None:
    route = respx.post(f"{API}/v1/knowledge/wiki/command").mock(return_value=_ok("| a | b |"))

    result = CliRunner().invoke(
        app,
        [
            "kb",
            "rows",
            "--table",
            "connections",
            "--entity",
            "Stripe",
            "--start",
            "2026-01-01",
            "--current-only",
            "--desc",
            "--limit",
            "5",
            "--evidence",
            "--scope",
            "Platform",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "| a | b |" in result.output
    assert _body(route) == {
        "command": "rows",
        "table": "connections",
        "entity": "Stripe",
        "start_date": "2026-01-01",
        "current_only": True,
        "descending": True,
        "limit": 5,
        "include_evidence": True,
        "scope": "Platform",
    }


@respx.mock
def test_rows_sends_the_database_arguments_by_their_door_names() -> None:
    # as_of, to, connected_to and group_by are rows arguments the SDK's rows()
    # does not name; the CLI sends them through the generic door call.
    route = respx.post(f"{API}/v1/knowledge/wiki/command").mock(return_value=_ok())

    result = CliRunner().invoke(
        app,
        [
            "kb",
            "rows",
            "--as-of",
            "2024-06-30",
            "--connected-to",
            "Northwind Fund",
            "--group-by",
            "entity",
            "--order-by",
            "amount",
            "--scope",
            "Platform",
        ],
    )

    assert result.exit_code == 0, result.output
    assert _body(route) == {
        "command": "rows",
        "table": "facts",
        "as_of": "2024-06-30",
        "connected_to": "Northwind Fund",
        "group_by": "entity",
        "order_by": "amount",
        "scope": "Platform",
    }


@respx.mock
def test_rows_sends_match_and_refuses_an_unknown_mode() -> None:
    route = respx.post(f"{API}/v1/knowledge/wiki/command").mock(return_value=_ok())

    sent = CliRunner().invoke(
        app, ["kb", "rows", "--attribute", "start date", "--match", "similar"]
    )
    refused = CliRunner().invoke(
        app, ["kb", "rows", "--attribute", "start date", "--match", "fuzzy"]
    )

    assert sent.exit_code == 0, sent.output
    assert _body(route)["match"] == "similar"
    assert refused.exit_code != 0
    assert route.call_count == 1


@respx.mock
def test_rows_scope_defaults_to_the_bound_project(monkeypatch) -> None:
    monkeypatch.setenv("BEAKR_PROJECT_ID", "proj-1")
    route = respx.post(f"{API}/v1/knowledge/wiki/command").mock(return_value=_ok())

    result = CliRunner().invoke(app, ["kb", "rows"])

    assert result.exit_code == 0, result.output
    assert _body(route)["scope"] == "proj-1"


@respx.mock
def test_add_fact_stages_a_write() -> None:
    route = respx.post(f"{API}/v1/knowledge/wiki/write").mock(
        return_value=_ok("Proposal abc staged.")
    )

    result = CliRunner().invoke(
        app,
        [
            "kb",
            "add-fact",
            "Stripe",
            "--attribute",
            "renewal",
            "--value",
            "2027-01-31",
            "--evidence",
            "{{doc:1}}",
            "--when",
            "2026-10",
            "--rationale",
            "order form",
            "--scope",
            "Platform",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Proposal abc staged." in result.output
    assert _body(route) == {
        "action": "add_fact",
        "page": "Stripe",
        "attribute": "renewal",
        "value": "2027-01-31",
        "evidence": "{{doc:1}}",
        "when": "2026-10",
        "rationale": "order form",
        "scope": "Platform",
    }


@respx.mock
def test_add_connection_body() -> None:
    route = respx.post(f"{API}/v1/knowledge/wiki/write").mock(return_value=_ok())

    result = CliRunner().invoke(
        app,
        [
            "kb",
            "add-connection",
            "Stripe",
            "--relation",
            "bills",
            "--to",
            "Acme",
            "--evidence",
            "{{doc:1}}",
            "--rationale",
            "invoice",
        ],
    )

    assert result.exit_code == 0, result.output
    body = _body(route)
    assert body["action"] == "add_connection"
    assert (body["relation"], body["to"]) == ("bills", "Acme")
    assert "scope" not in body


@respx.mock
def test_add_finding_body_and_repeated_about() -> None:
    route = respx.post(f"{API}/v1/knowledge/wiki/write").mock(return_value=_ok())

    result = CliRunner().invoke(
        app,
        [
            "kb",
            "add-finding",
            "Stripe",
            "--date",
            "2026-10-01",
            "--kind",
            "decision",
            "--text",
            "Stay on Stripe",
            "--evidence",
            "{{conversation:1}}",
            "--about",
            "billing",
            "--about",
            "vendors",
            "--rationale",
            "meeting",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    body = _body(route)
    assert body["action"] == "add_finding"
    assert body["kind"] == "decision"
    assert body["about"] == ["billing", "vendors"]
    assert json.loads(result.output)["outcome"]["status"] == "success"


def test_add_finding_rejects_an_unknown_kind() -> None:
    result = CliRunner().invoke(
        app,
        [
            "kb",
            "add-finding",
            "Stripe",
            "--date",
            "2026-10-01",
            "--kind",
            "hunch",
            "--text",
            "x",
            "--evidence",
            "y",
            "--rationale",
            "z",
        ],
    )
    assert result.exit_code == 2


@respx.mock
def test_edit_rows_without_replacement_rows_deletes() -> None:
    route = respx.post(f"{API}/v1/knowledge/wiki/write").mock(return_value=_ok())

    result = CliRunner().invoke(
        app,
        [
            "kb",
            "edit-rows",
            "Stripe",
            "--table",
            "facts",
            "--old-row",
            '{"attribute": "renewal", "value": "2026"}',
            "--reason",
            "duplicate",
            "--rationale",
            "cleanup",
        ],
    )

    assert result.exit_code == 0, result.output
    body = _body(route)
    assert body["action"] == "edit_rows"
    assert body["old_rows"] == [{"attribute": "renewal", "value": "2026"}]
    # An explicit empty list is what tells the door to delete.
    assert body["rows"] == []
    assert body["reason"] == "duplicate"


@respx.mock
def test_edit_rows_accepts_an_edits_file(tmp_path) -> None:
    edits = [{"old_rows": ["renewal: 2026"], "rows": [], "reason": "dup"}]
    path = tmp_path / "edits.json"
    path.write_text(json.dumps(edits))
    route = respx.post(f"{API}/v1/knowledge/wiki/write").mock(return_value=_ok())

    result = CliRunner().invoke(
        app,
        [
            "kb",
            "edit-rows",
            "Stripe",
            "--table",
            "facts",
            "--edits",
            f"@{path}",
            "--rationale",
            "r",
        ],
    )

    assert result.exit_code == 0, result.output
    assert _body(route)["edits"] == edits


@respx.mock
def test_a_door_refusal_exits_1_with_its_guidance() -> None:
    respx.post(f"{API}/v1/knowledge/wiki/write").mock(
        return_value=httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "Stripe is not a card; use edit_section."}],
                "outcome": {"status": "error", "error_code": "not_a_card"},
            },
        )
    )

    result = CliRunner().invoke(
        app,
        [
            "kb",
            "add-fact",
            "Stripe",
            "--attribute",
            "a",
            "--value",
            "v",
            "--evidence",
            "e",
            "--rationale",
            "r",
        ],
    )

    assert result.exit_code == 1
    assert "not a card" in result.output


_SUPERSEDES_REFUSAL = (
    '"Nothing like this" does not name a finding on FXC Battery Trial. Its findings are: '
    "2026-10-10 \u00b7 result. Nothing was written."
)


def _add_finding(*extra: str):
    return CliRunner().invoke(
        app,
        [
            "kb",
            "add-finding",
            "FXC Battery Trial",
            "--date",
            "2026-10-12",
            "--kind",
            "result",
            "--text",
            "t",
            "--evidence",
            "e",
            "--supersedes",
            "Nothing like this",
            "--rationale",
            "r",
            *extra,
        ],
    )


@respx.mock
def test_an_argument_refusal_is_a_usage_error_without_an_http_status() -> None:
    # Root cause: the door answers a refusal in a 200 body; the SDK's error
    # text labelled it "(HTTP 200, invalid_arguments)" and the CLI exited 1.
    respx.post(f"{API}/v1/knowledge/wiki/write").mock(
        return_value=httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": _SUPERSEDES_REFUSAL}],
                "outcome": {"status": "error", "error_code": "invalid_arguments"},
            },
        )
    )

    result = _add_finding()
    as_json = _add_finding("--json")

    assert result.exit_code == 2, result.output
    assert "Nothing was written. (invalid_arguments)" in " ".join(result.output.split())
    assert "HTTP" not in result.output
    assert as_json.exit_code == 2
    error = json.loads(as_json.stdout)["error"]
    assert error["code"] == "invalid_arguments"
    assert error["exit_code"] == 2
    assert "status" not in error


@respx.mock
def test_query_sends_the_json_spec_through_the_command_door(tmp_path) -> None:
    route = respx.post(f"{API}/v1/knowledge/wiki/command").mock(return_value=_ok())
    spec = {"from": "facts", "where": {"field": "Status"}, "group_by": ["value"]}

    inline = CliRunner().invoke(app, ["kb", "query", json.dumps(spec), "--scope", "Platform"])
    path = tmp_path / "q.json"
    path.write_text(json.dumps(spec))
    from_file = CliRunner().invoke(app, ["kb", "query", "--file", str(path)])

    assert inline.exit_code == 0, inline.output
    assert from_file.exit_code == 0, from_file.output
    assert json.loads(route.calls[0].request.content) == {
        "command": "query",
        "spec": spec,
        "scope": "Platform",
    }
    assert json.loads(route.calls[1].request.content)["spec"] == spec


@respx.mock
def test_query_sends_sql_with_a_cursor() -> None:
    route = respx.post(f"{API}/v1/knowledge/wiki/command").mock(return_value=_ok())
    sql = "SELECT value, COUNT(*) AS n FROM facts WHERE field = 'Status' GROUP BY value"

    result = CliRunner().invoke(app, ["kb", "query", sql, "--cursor", "abc"])

    assert result.exit_code == 0, result.output
    assert json.loads(route.calls[0].request.content) == {
        "command": "query",
        "sql": sql,
        "cursor": "abc",
    }


def test_query_refuses_a_json_spec_that_is_not_json_before_sending() -> None:
    result = CliRunner().invoke(app, ["kb", "query", '{"from": facts}'])

    assert result.exit_code == 1
    assert "not valid JSON" in result.output


_PLATFORM = "11111111-2222-3333-4444-555555555555"

# Each command's argv, without --scope, and the door it posts to.
_SCOPED_COMMANDS = [
    (["kb", "rows"], "command"),
    (["kb", "query", "SELECT value FROM facts"], "command"),
    (
        ["kb", "add-fact", "Stripe", "--attribute", "a", "--value", "v"]
        + ["--evidence", "{{doc:1}}", "--rationale", "r"],
        "write",
    ),
    (
        ["kb", "add-connection", "Stripe", "--relation", "bills", "--to", "Acme"]
        + ["--evidence", "{{doc:1}}", "--rationale", "r"],
        "write",
    ),
    (
        ["kb", "add-finding", "Stripe", "--date", "2026-10-01", "--kind", "result"]
        + ["--text", "t", "--evidence", "{{doc:1}}", "--rationale", "r"],
        "write",
    ),
    (
        ["kb", "edit-rows", "Stripe", "--table", "facts", "--old-row", "renewal: 2026"]
        + ["--reason", "duplicate", "--rationale", "r"],
        "write",
    ),
]


# Root cause: kb_cards._scope passed --scope through unchanged while every other
# project option resolves a slug or name to the project id, so a slug the door
# could not match failed these commands. They now resolve like the rest.
@pytest.mark.real_project_refs
@pytest.mark.parametrize(
    ("argv", "door"), _SCOPED_COMMANDS, ids=[argv[1] for argv, _ in _SCOPED_COMMANDS]
)
@respx.mock
def test_card_commands_resolve_a_project_slug_to_its_id(
    argv: list[str], door: str, monkeypatch
) -> None:
    # Resolutions are cached per process; another test's "platform-docs" must not answer.
    monkeypatch.setattr("beakr_cli.project_ref._resolved", {})
    respx.get(f"{API}/v1/projects").respond(
        json=[{"id": _PLATFORM, "name": "Platform Docs", "slug": "platform-docs"}]
    )
    route = respx.post(f"{API}/v1/knowledge/wiki/{door}").mock(return_value=_ok())

    result = CliRunner().invoke(app, [*argv, "--scope", "platform-docs"])

    assert result.exit_code == 0, result.output
    assert _body(route)["scope"] == _PLATFORM


# Root cause: a capped rows scan names its continuation as after_card=<card>
# (the engine's cursor argument for rows), which the CLI had no option for.
@respx.mock
def test_rows_continues_a_capped_scan_after_a_card() -> None:
    route = respx.post(f"{API}/v1/knowledge/wiki/command").mock(return_value=_ok())

    result = CliRunner().invoke(app, ["kb", "rows", "--after-card", "Stripe"])

    assert result.exit_code == 0, result.output
    assert _body(route)["after_card"] == "Stripe"


_ROW_WRITES = {
    "add-fact": ["--attribute", "Owner", "--value", "Rowan Vale"],
    "add-connection": ["--relation", "Depends on", "--to", "Tern Platform"],
    "add-finding": ["--date", "2026-10-21", "--kind", "decision", "--text", "Ship it."],
}


@pytest.mark.parametrize("command", sorted(_ROW_WRITES))
@respx.mock
def test_a_row_write_can_cite_this_conversation(command: str) -> None:
    # Root cause: a row write could only name a stored source, so
    # --evidence "{{conversation:x}}" was refused "Not citeable" with nothing
    # to declare it by. It now sends the MCP shape: the token in the evidence
    # and an inline conversation citation with source_title and meta.excerpt.
    route = respx.post(f"{API}/v1/knowledge/wiki/write").mock(return_value=_ok())

    result = CliRunner().invoke(
        app,
        [
            "kb",
            command,
            "Plover Gadget",
            *_ROW_WRITES[command],
            "--cite-conversation",
            "call-with-ana",
            "--excerpt",
            "Ana said Rowan Vale owns it.",
            "--source-title",
            "Call with Ana",
            "--rationale",
            "r",
        ],
    )

    assert result.exit_code == 0, result.output
    body = _body(route)
    assert body["action"] == command.replace("-", "_")
    assert body["evidence"] == "{{conversation:call-with-ana}}"
    assert body["citations"] == [
        {
            "key": "conversation:call-with-ana",
            "source_type": "conversation",
            "source_title": "Call with Ana",
            "meta": {"excerpt": "Ana said Rowan Vale owns it."},
        }
    ]


@respx.mock
def test_a_cited_conversation_joins_other_evidence_and_defaults_its_title() -> None:
    route = respx.post(f"{API}/v1/knowledge/wiki/write").mock(return_value=_ok())

    result = CliRunner().invoke(
        app,
        [
            "kb", "add-fact", "Plover Gadget", *_ROW_WRITES["add-fact"],
            "--evidence", "plover-memo.md › para 2",
            "--cite-conversation", "{{conversation:standup}}", "--excerpt", "Rowan owns it.",
            "--rationale", "r",
        ],
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    body = _body(route)
    assert body["evidence"] == "plover-memo.md › para 2 {{conversation:standup}}"
    assert body["citations"][0]["source_title"] == "standup"


@pytest.mark.parametrize(
    "extra",
    [
        [],  # no source at all
        ["--cite-conversation", "standup"],  # no excerpt
        ["--cite-conversation", "two words", "--excerpt", "x"],  # not a label
        ["--evidence", "{{rag:1}}", "--excerpt", "x"],  # excerpt without a conversation
    ],
)
@respx.mock
def test_a_row_write_without_a_usable_source_is_a_usage_error(extra: list[str]) -> None:
    route = respx.post(f"{API}/v1/knowledge/wiki/write").mock(return_value=_ok())

    result = CliRunner().invoke(
        app,
        ["kb", "add-fact", "Plover Gadget", *_ROW_WRITES["add-fact"], *extra, "--rationale", "r"],
    )

    assert result.exit_code == 2, result.output
    assert not route.called


def test_rows_to_help_says_it_lists_incoming_connections() -> None:
    # Root cause: to= returns the rows pointing at the page, but the help said
    # "every row involving this page".
    result = CliRunner().invoke(app, ["kb", "rows", "--help"], env={"COLUMNS": "200"})

    text = " ".join(result.output.split())
    assert "the rows pointing at this page (its incoming links)" in text
    assert "involving" not in text
