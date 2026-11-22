"""``beakr kb compiles ls|cancel``: count and cancel queued Knowledge compiles (ENG-2451).

Max asked (2026-11-21) for a way to stop what Beakr is compiling: the
Knowledge compiles still queued for a project, one source or one file. These
tests pin what each command sends to the engine's documented routes
(beakr-engine #3078, shapes ``CompileCancelOut`` and ``CompileQueueOut``), how
the answers read, and the exit codes a script branches on.
"""

from __future__ import annotations

import json

import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.main import app

API = "https://api.test"
PROJECT = "p1"
COMPILES = f"{API}/v1/projects/{PROJECT}/knowledge/compiles"
runner = CliRunner()


@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


@pytest.fixture
def at_terminal(monkeypatch):
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)


def _compiles_cancelled(**fields) -> dict:
    return {
        "cancel_id": "c1",
        "scope": "project",
        "status": "cancelling",
        "will_be_skipped": 40,
        "already_running": 2,
        "sync_stopped": False,
        "syncs_stopped": 0,
        "sync_job_id": None,
        "next_sync": "The next sync queues them again.",
        "note": "Pages Knowledge already wrote stay.",
        **fields,
    }


def _queue(**fields) -> dict:
    return {"queued": 12, "running": 1, "last_cancel": None, **fields}


# -- kb compiles ls ----------------------------------------------------------------------


@respx.mock
def test_compiles_ls_counts_the_project_and_reports_the_last_cancel(at_terminal):
    last = {
        "cancel_id": "c1",
        "status": "cancelling",
        "done": 12,
        "total": 40,
        "summary": "Cancelling: 12 of 40 queued compiles skipped",
        "cancelled_by": "Ada Lovelace",
        "cancelled_at": "2026-11-21T18:00:00Z",
    }
    route = respx.get(COMPILES).respond(200, json=_queue(last_cancel=last))
    result = runner.invoke(app, ["kb", "compiles", "ls", "-P", PROJECT])
    assert result.exit_code == 0, result.output
    assert not route.calls.last.request.url.params
    assert "12 compiles queued and 1 running in the project." in result.output
    assert (
        "Last cancel by Ada Lovelace at 2026-11-21T18:00:00Z: "
        "Cancelling: 12 of 40 queued compiles skipped"
    ) in result.output


@pytest.mark.parametrize(
    ("flag", "param"), [("--source", "attachment_id"), ("--file", "artifact_id")]
)
@respx.mock
def test_compiles_ls_for_one_source_or_file(flag, param):
    route = respx.get(COMPILES).respond(200, json=_queue(queued=3, running=0))
    result = runner.invoke(app, ["kb", "compiles", "ls", flag, "x1", "-P", PROJECT])
    assert result.exit_code == 0, result.output
    assert dict(route.calls.last.request.url.params) == {param: "x1"}
    line = result.stdout.splitlines()[0].split("\t")
    assert line[2:4] == ["queued=3", "running=0"]
    assert f"next: beakr kb compiles cancel {flag} x1 -P {PROJECT}" in result.stdout


def test_compiles_ls_takes_a_source_or_a_file_not_both():
    result = runner.invoke(
        app, ["kb", "compiles", "ls", "--source", "s1", "--file", "f1", "-P", PROJECT]
    )
    assert result.exit_code == 2
    assert "not both" in result.output


@respx.mock
def test_compiles_ls_json_is_the_answer():
    respx.get(COMPILES).respond(200, json=_queue())
    result = runner.invoke(app, ["kb", "compiles", "ls", "-P", PROJECT, "--json"])
    assert json.loads(result.stdout) == _queue()


# -- kb compiles cancel ------------------------------------------------------------------


@respx.mock
def test_compiles_cancel_for_the_project_sends_an_empty_body(at_terminal):
    route = respx.post(f"{COMPILES}/cancel").respond(202, json=_compiles_cancelled())
    result = runner.invoke(app, ["kb", "compiles", "cancel", "-P", PROJECT, "--yes"])
    assert result.exit_code == 0, result.output
    assert json.loads(route.calls.last.request.content) == {}
    assert "Cancelling 40 queued compiles in the project." in result.output
    assert "2 compiles already running are left to finish." in result.output
    assert "Pages Knowledge already wrote stay." in result.output
    assert "running sync" not in result.output


@respx.mock
def test_compiles_cancel_for_a_source_also_reports_its_stopped_sync(at_terminal):
    route = respx.post(f"{COMPILES}/cancel").respond(
        202,
        json=_compiles_cancelled(
            scope="source",
            will_be_skipped=1,
            already_running=0,
            sync_stopped=True,
            syncs_stopped=1,
            sync_job_id="j9",
        ),
    )
    result = runner.invoke(
        app, ["kb", "compiles", "cancel", "--source", "s1", "-P", PROJECT, "--yes"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(route.calls.last.request.content) == {"attachment_id": "s1"}
    assert "Cancelling 1 queued compile for source s1." in result.output
    assert "already running" not in result.output
    said = " ".join(result.output.split())
    assert (
        "Also stopped 1 running sync (job j9): "
        "items not yet handed to Knowledge will not be queued."
    ) in said


@respx.mock
def test_compiles_cancel_for_a_file_names_the_artifact():
    route = respx.post(f"{COMPILES}/cancel").respond(
        202, json=_compiles_cancelled(scope="source", status="cancelled", will_be_skipped=0)
    )
    result = runner.invoke(app, ["kb", "compiles", "cancel", "--file", "f1", "-P", PROJECT, "-y"])
    assert result.exit_code == 0, result.output
    assert json.loads(route.calls.last.request.content) == {"artifact_id": "f1"}
    fields = result.stdout.splitlines()[0].split("\t")
    assert fields == [
        "c1",
        "cancelled",
        "scope=source",
        "will_be_skipped=0",
        "already_running=2",
        "syncs_stopped=0",
    ]
    assert f"next: beakr kb compiles ls --file f1 -P {PROJECT}" in result.stdout


@respx.mock
def test_compiles_cancel_json_is_the_answer():
    respx.post(f"{COMPILES}/cancel").respond(202, json=_compiles_cancelled())
    result = runner.invoke(app, ["kb", "compiles", "cancel", "-P", PROJECT, "-y", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == _compiles_cancelled()


@respx.mock
def test_compiles_cancel_without_yes_off_a_terminal_sends_nothing():
    route = respx.post(f"{COMPILES}/cancel").respond(202, json=_compiles_cancelled())
    result = runner.invoke(app, ["kb", "compiles", "cancel", "-P", PROJECT])
    assert result.exit_code == 2
    assert not route.called


@pytest.mark.parametrize(("status", "exit_code"), [(403, 3), (404, 4)])
@respx.mock
def test_compiles_cancel_refusals_exit_with_their_codes(status, exit_code):
    respx.post(f"{COMPILES}/cancel").respond(status, json={"detail": "no"})
    result = runner.invoke(app, ["kb", "compiles", "cancel", "--source", "s1", "-P", PROJECT, "-y"])
    assert result.exit_code == exit_code, result.output


def test_compiles_cancel_takes_a_source_or_a_file_not_both():
    result = runner.invoke(
        app, ["kb", "compiles", "cancel", "--source", "s1", "--file", "f1", "-P", PROJECT, "-y"]
    )
    assert result.exit_code == 2
    assert "not both" in result.output


def test_compiles_commands_need_a_project(isolated_home):
    result = runner.invoke(app, ["kb", "compiles", "ls"])
    assert result.exit_code == 2
    assert "beakr init" in result.output


@respx.mock
def test_compiles_cancel_for_the_project_reports_every_sync_it_stopped(at_terminal):
    respx.post(f"{COMPILES}/cancel").respond(
        202, json=_compiles_cancelled(sync_stopped=True, syncs_stopped=3)
    )
    result = runner.invoke(app, ["kb", "compiles", "cancel", "-P", PROJECT, "--yes"])
    assert result.exit_code == 0, result.output
    said = " ".join(result.output.split())
    assert "Also stopped 3 running syncs:" in said
    assert "(job" not in said


def test_compiles_cancel_of_the_project_asks_about_its_running_syncs(monkeypatch):
    asked = []

    def confirm(prompt, yes):
        asked.append(prompt)
        raise SystemExit(1)

    monkeypatch.setattr("beakr_cli.commands.kb_compiles.confirm_or_require_yes", confirm)
    runner.invoke(app, ["kb", "compiles", "cancel", "-P", PROJECT])
    runner.invoke(app, ["kb", "compiles", "cancel", "--source", "s1", "-P", PROJECT])
    runner.invoke(app, ["kb", "compiles", "cancel", "--file", "f1", "-P", PROJECT])
    assert asked == [
        "Cancel the queued Knowledge compiles in the project and stop every running sync in it?",
        "Cancel the queued Knowledge compiles for source s1 and stop its running sync?",
        "Cancel the queued Knowledge compiles for file f1?",
    ]
