"""Exit codes, JSON errors, project flags and readable output (tester round 2).

Each test names the behavior a tester found broken; the comment on each says
why it was.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.main import internal_app as app

API = "https://api.test"
PROJECT = "22222222-2222-2222-2222-222222222222"
runner = CliRunner()


@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


@pytest.fixture
def terminal(monkeypatch):
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)


def _door(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "outcome": {"status": "success"}}


# -- errors under --json ------------------------------------------------------


# Root cause: a failed command printed its error as plain text on stderr even
# with --json, so a script reading stdout got nothing to parse. Under --json
# the error is a JSON object on stdout, beside the exit code.
@respx.mock
def test_an_api_error_under_json_is_a_json_object_on_stdout():
    respx.get(f"{API}/v1/jobs/j-gone").respond(
        404, json={"error": {"code": "job_not_found", "message": "No such job."}}
    )

    result = runner.invoke(app, ["jobs", "show", "j-gone", "--json"])

    assert result.exit_code == 4, result.output
    error = json.loads(result.stdout)["error"]
    assert error["code"] == "job_not_found"
    assert error["exit_code"] == 4
    assert error["status"] == 404


def test_a_usage_error_under_json_is_a_json_object_on_stdout():
    result = runner.invoke(app, ["labels", "ls", "--json", "--no-such-flag"])

    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["code"] == "usage"


# Root cause: `projects profile show` with no profile printed plain text and
# exited 4 through typer.Exit, past the error handler, even with --json.
@respx.mock
def test_profile_show_without_a_profile_is_a_json_not_found_error():
    respx.get(f"{API}/v1/projects/{PROJECT}/knowledge-profile").respond(
        404, json={"error": {"code": "knowledge_profile_not_found", "message": "none"}}
    )

    result = runner.invoke(app, ["projects", "profile", "show", "-P", PROJECT, "--json"])

    assert result.exit_code == 4, result.output
    error = json.loads(result.stdout)["error"]
    assert error["code"] == "knowledge_profile_not_found"
    assert "has no Knowledge profile yet" in error["message"]


@respx.mock
def test_labels_rm_of_a_missing_label_exits_not_found():
    respx.get(f"{API}/v1/labels").respond(json={"data": [], "has_more": False})
    result = runner.invoke(app, ["labels", "rm", "nope", "--yes"])
    assert result.exit_code == 4, result.output


# -- -P everywhere -------------------------------------------------------------


# Root cause: `projects members ls` took the project only as a positional, so
# `-P <id>`, the flag every other command takes, was a usage error.
@respx.mock
def test_projects_members_ls_takes_the_project_as_P():
    respx.get(f"{API}/v1/projects").respond(json=[{"id": PROJECT, "name": "Assays"}])
    members = respx.get(f"{API}/v1/projects/{PROJECT}/members").respond(json=[])

    result = runner.invoke(app, ["projects", "members", "ls", "-P", PROJECT, "--json"])

    assert result.exit_code == 0, result.output
    assert members.called


# Root cause: the card commands took --scope/-s only, while every other
# command names its project with -P/--project.
@respx.mock
def test_card_commands_take_the_project_as_P():
    door = respx.post(f"{API}/v1/knowledge/wiki/command").respond(json=_door("ok"))

    result = runner.invoke(app, ["kb", "rows", "-P", PROJECT])

    assert result.exit_code == 0, result.output
    assert json.loads(door.calls.last.request.content)["scope"] == PROJECT


@respx.mock
def test_proposals_show_with_P_refuses_a_proposal_from_another_project():
    respx.get(f"{API}/v1/knowledge/wiki/proposals/prop-1").respond(
        json={"id": "prop-1", "status": "pending", "project_id": "other"}
    )

    result = runner.invoke(app, ["kb", "proposals", "show", "prop-1", "-P", PROJECT])

    assert result.exit_code == 4, result.output


# -- readable output -----------------------------------------------------------


# Root cause: piped, `kb proposals ls` printed id and status only, and with no
# --cursor the list stopped at one page with nothing saying more existed.
@respx.mock
def test_proposals_ls_piped_names_type_page_and_summary_and_the_next_page():
    route = respx.get(f"{API}/v1/knowledge/wiki/proposals").respond(
        json={
            "data": [
                {
                    "id": "prop-1",
                    "proposal_type": "edit",
                    "status": "pending",
                    "page_title": "Onboarding",
                    "summary": "Extend pairing",
                }
            ],
            "has_more": True,
            "next_cursor": "c2",
        }
    )

    first = runner.invoke(app, ["kb", "proposals", "ls", "-n", "1"])
    second = runner.invoke(app, ["kb", "proposals", "ls", "--cursor", "c2"])

    assert first.exit_code == 0, first.output
    assert first.stdout.splitlines() == [
        "prop-1\ttype=edit\tstatus=pending\tpage=Onboarding\tExtend pairing",
        "next: beakr kb proposals ls --cursor c2",
    ]
    assert second.exit_code == 0, second.output
    assert route.calls.last.request.url.params["cursor"] == "c2"


# Root cause: `kb log` with no page printed the door's reply envelope (piped,
# a key=value dump of content/outcome) instead of the log's text.
@respx.mock
def test_kb_log_without_a_page_prints_the_log_text():
    respx.post(f"{API}/v1/knowledge/wiki/command").respond(json=_door("rev 4  Launch Plan"))

    result = runner.invoke(app, ["kb", "log"])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[0] == "rev 4  Launch Plan"
    assert "outcome" not in result.stdout


# Root cause: search snippets are cut from stored markdown, so section markers,
# {{artifact:...}} citation tokens and [[links]] were printed as they are stored.
@respx.mock
@pytest.mark.parametrize("as_terminal", [True, False], ids=["terminal", "piped"])
def test_kb_search_snippets_are_prose_without_markup(monkeypatch, as_terminal):
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: not as_terminal)
    respx.get(f"{API}/v1/knowledge/wiki/search").respond(
        json={
            "data": [
                {
                    "id": "p1",
                    "title": "Helios",
                    "score": 0.9,
                    "content_snippet": "<!-- sec:sec_a -->\n## Lead\n\nNW-217 chosen "
                    "by [[Dana Whitfield|Dana]].{{artifact:abc-document-block-1}}",
                }
            ],
            "has_more": False,
        }
    )

    result = runner.invoke(app, ["kb", "search", "lead"])

    assert result.exit_code == 0, result.output
    assert "<!--" not in result.stdout and "{{" not in result.stdout
    assert "## Lead NW-217 chosen by Dana." in result.stdout


# Root cause: `runs show` printed the run's state only, so a finished run's
# answer could not be read back without asking again.
@respx.mock
@pytest.mark.parametrize("as_terminal", [True, False], ids=["terminal", "piped"])
def test_runs_show_prints_the_answer_and_its_sources(monkeypatch, as_terminal):
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: not as_terminal)
    respx.get(f"{API}/v1/runs/r1").respond(
        json={
            "run_id": "r1",
            "thread_id": "t1",
            "status": "completed",
            "answer": {
                "text": "NW-217 was chosen.[1]",
                "raw_text": "NW-217 was chosen.{{doc:a1}}",
                "sources": [{"name": "memo.pdf", "location": {"url": "https://x.test/m"}}],
            },
        }
    )

    result = runner.invoke(app, ["runs", "show", "r1"])

    assert result.exit_code == 0, result.output
    assert "NW-217 was chosen.[1]" in result.stdout
    assert "[1] memo.pdf" in result.stdout


# Root cause: the revoke route answers with no body, and --json printed `null`.
@respx.mock
def test_interview_link_revoke_json_says_what_was_revoked():
    respx.get(f"{API}/v1/interview/sendables").respond(json=[])
    respx.post(f"{API}/v1/interview/sendables/links/l1/revoke").mock(
        return_value=httpx.Response(204)
    )

    result = runner.invoke(app, ["interviews", "link", "revoke", "l1", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"link_id": "l1", "revoked": True}


# Root cause: piped, `hooks status` (like doctor) was one line of nested JSON
# columns; it is one line per client now.
def test_hooks_status_piped_is_one_line_per_client():
    result = runner.invoke(app, ["hooks", "status"])

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[0].startswith("claude\tevents=")
    assert lines[1].startswith("codex\tinstalled=")
    assert "{" not in result.stdout


# Root cause: a project membership names its member `principal_id`, which was not
# one of the compact output's id keys, so piped `projects members ls` printed "-"
# where the member id belongs and nothing a script could pass to `members rm`.
@respx.mock
def test_projects_members_ls_piped_prints_each_member_id():
    respx.get(f"{API}/v1/projects").respond(json=[{"id": PROJECT, "name": "Assays"}])
    member = {
        "principal_type": "user",
        "principal_id": "33333333-3333-3333-3333-333333333333",
        "role": "owner",
        "name": "Local evaluation administrator",
    }
    respx.get(f"{API}/v1/projects/{PROJECT}/members").respond(json=[member])

    result = runner.invoke(app, ["projects", "members", "ls", "-P", PROJECT])

    assert result.exit_code == 0, result.output
    first = result.stdout.splitlines()[0].split("\t")
    assert first[:2] == [member["principal_id"], "Local evaluation administrator"]


# Root cause: the card commands (kb rows, kb query, ...) took -s for the project
# and the rest of kb did not, so `kb log -s` and `kb proposals ls -s` were usage
# errors. Every kb command that takes a project now spells it -P, --scope or -s.
def test_every_kb_command_that_takes_a_project_takes_it_as_P_and_s():
    import click
    import typer.main

    def leaves(command, path):
        if isinstance(command, click.Group):
            for name, sub in command.commands.items():
                yield from leaves(sub, [*path, name])
        else:
            yield " ".join(path), command

    kb = typer.main.get_command(app).commands["kb"]
    checked = []
    for name, command in leaves(kb, ["kb"]):
        for param in command.params:
            if "--project" in getattr(param, "opts", []):
                assert {"-P", "--scope", "-s"} <= set(param.opts), name
                checked.append(name)
    assert {"kb log", "kb proposals ls", "kb rows"} <= set(checked)


@respx.mock
def test_kb_proposals_ls_takes_the_project_as_s():
    proposals = respx.get(f"{API}/v1/knowledge/wiki/proposals").respond(
        json={"data": [], "has_more": False, "next_cursor": None}
    )

    result = runner.invoke(app, ["kb", "proposals", "ls", "-s", PROJECT, "--json"])

    assert result.exit_code == 0, result.output
    assert proposals.calls.last.request.url.params["project_id"] == PROJECT
