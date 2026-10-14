"""``beakr ask``, ``runs``, ``workflows``, ``outputs`` and ``interviews`` (mocked routes)."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.main import internal_app as app
from tests.upload_routes import mock_resumable_uploads

API = "https://api.test"
S3 = "https://s3.test/bucket"
runner = CliRunner()


@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


@pytest.fixture
def rendered(monkeypatch):
    """Render tables, as in a terminal, instead of JSON for a pipe."""
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)


def _sse(*events: dict) -> httpx.Response:
    body = "".join(f"id: {i}\ndata: {json.dumps(e)}\n\n" for i, e in enumerate(events, 1))
    return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})


def _mock_run(*events: dict) -> respx.Route:
    start = respx.post(f"{API}/v1/runs").respond(
        200, json={"run_id": "r1", "thread_id": "t1", "status": "running"}
    )
    respx.get(f"{API}/v1/runs/r1/events").mock(return_value=_sse(*events))
    return start


# -- ask ---------------------------------------------------------------------


@respx.mock
def test_ask_streams_the_answer_and_numbers_its_citations():
    start = _mock_run(
        {"type": "content_delta", "data": {"delta": "thinking", "is_thinking": True}},
        {"type": "content_delta", "data": {"delta": "Binding uses {{doc:a1}} and "}},
        {"type": "content_delta", "data": {"delta": "{{wiki:p2}}, again {{doc:a1}}."}},
        {
            "type": "sources",
            "data": {
                "sources": [
                    {
                        "key": "doc:a1",
                        "type": "doc",
                        "title": "Assay SOP.pdf",
                        "meta": {"web_view_url": "https://app.test/f/a1"},
                    },
                    {"key": "wiki:p2", "type": "wiki", "title": "Binding assay"},
                ]
            },
        },
        {"type": "stream_complete", "data": {"status": "completed"}},
    )
    result = runner.invoke(app, ["ask", "How is binding run?", "--project", "p1"])
    assert result.exit_code == 0, result.output
    assert "Binding uses [1] and [2], again [1]." in result.output
    assert "thinking" not in result.output
    # Each source says where it lives: its name, then the link to the original.
    assert "[1] Assay SOP.pdf · https://app.test/f/a1" in result.output
    assert "[2] Binding assay" in result.output
    body = json.loads(start.calls.last.request.content)
    assert body["message"] == "How is binding run?"
    assert body["home_project_id"] == "p1"


@respx.mock
def test_ask_uploads_files_and_gives_them_to_the_run_by_id(tmp_path):
    """The run's ``files`` carries the uploads (audit SPEC-321).

    The CLI used to paste "notes.md (file id a9)" into the prompt, so the agent
    saw a file name in text and was never given the file itself.
    """
    # With no project the file goes to the caller's personal project.
    respx.get(f"{API}/v1/projects").respond(
        200,
        json={
            "data": [{"id": "p-me", "project_type": "personal"}],
            "next_cursor": None,
            "has_more": False,
        },
    )
    mock_resumable_uploads(API)
    start = _mock_run({"type": "stream_complete", "data": {"status": "completed"}})
    notes = tmp_path / "notes.md"
    notes.write_text("hello")
    result = runner.invoke(app, ["ask", "Summarize this", "--file", str(notes)])
    assert result.exit_code == 0, result.output
    body = json.loads(start.calls.last.request.content)
    assert body["files"] == ["art-notes.md"]
    assert body["message"] == "Summarize this"


@respx.mock
def test_ask_exits_nonzero_when_the_run_errors():
    # The stream_complete after an error carries its own status; it must not
    # reset the failure to success (the first version exited 0 here).
    _mock_run(
        {"type": "error", "data": {"message": "model unavailable"}},
        {"type": "stream_complete", "data": {"status": "failed"}},
    )
    result = runner.invoke(app, ["ask", "hi"])
    assert result.exit_code == 1
    assert "model unavailable" in result.output


@respx.mock
def test_ask_json_prints_each_event_as_a_line():
    _mock_run(
        {"type": "content_delta", "data": {"delta": "Hi"}},
        {"type": "stream_complete", "data": {"status": "completed"}},
    )
    result = runner.invoke(app, ["ask", "hi", "--json"])
    assert result.exit_code == 0, result.output
    lines = [json.loads(line) for line in result.output.splitlines() if line.startswith("{")]
    assert [e["type"] for e in lines] == ["content_delta", "stream_complete"]


# -- runs ----------------------------------------------------------------------


@respx.mock
def test_runs_ls_lists_threads(rendered):
    route = respx.get(f"{API}/v1/me/threads").respond(
        200,
        json={
            "data": [
                {
                    "thread_id": "t1",
                    "title": "Assay question",
                    "latest_message_at": "2026-10-09",
                    "latest_run_id": "r9",
                    "latest_run_status": "completed",
                }
            ],
            "next_cursor": None,
            "has_more": False,
        },
    )
    result = runner.invoke(app, ["runs", "ls", "--limit", "5"])
    assert result.exit_code == 0, result.output
    assert "Assay question" in result.output
    # The latest run id is what `runs show` / `runs cancel` take.
    assert "r9" in result.output
    assert route.calls.last.request.url.params["limit"] == "5"


@respx.mock
def test_runs_show_and_cancel():
    respx.get(f"{API}/v1/runs/r1").respond(
        200, json={"run_id": "r1", "thread_id": "t1", "status": "running"}
    )
    cancel = respx.post(f"{API}/v1/runs/r1/cancel").respond(204)
    shown = runner.invoke(app, ["runs", "show", "r1", "--json"])
    assert shown.exit_code == 0, shown.output
    assert json.loads(shown.output)["status"] == "running"
    cancelled = runner.invoke(app, ["runs", "cancel", "r1"])
    assert cancelled.exit_code == 0, cancelled.output
    assert cancel.called


# -- workflows -----------------------------------------------------------------


@respx.mock
def test_workflows_ls_lists_saved_configs():
    route = respx.get(f"{API}/v1/workflows/configs").respond(
        200, json=[{"id": "w1", "name": "Weekly digest", "run_count": 3}]
    )
    result = runner.invoke(app, ["workflows", "ls", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)[0]["id"] == "w1"
    assert route.calls.last.request.url.params["is_saved"] == "true"


@respx.mock
def test_workflows_create_saves_skills_scope_and_schedule(rendered):
    preview = respx.post(f"{API}/v1/workflows/schedule/preview").respond(
        200,
        json={
            "cron": "0 9 * * MON",
            "human_readable": "Mondays at 09:00",
            "upcoming_runs": ["2026-10-12T09:00"],
        },
    )
    create = respx.post(f"{API}/v1/workflows/configs").respond(
        201, json={"id": "w1", "name": "Weekly digest"}
    )
    result = runner.invoke(
        app,
        [
            "workflows", "create", "tpl1", "--name", "Weekly digest",
            "--input", "topic=assays", "--skill", "s1", "--project", "p1",
            "--cron", "0 9 * * MON", "--tz", "America/New_York",
        ],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    assert "Mondays at 09:00" in result.output
    body = json.loads(create.calls.last.request.content)
    assert body["template_id"] == "tpl1"
    assert body["inputs"] == {"topic": "assays"}
    assert body["meta"]["skill_ids"] == ["s1"]
    assert body["output_location_type"] == "project"
    assert body["output_location_id"] == "p1"
    assert body["schedule_cron"] == "0 9 * * MON"
    assert body["schedule_enabled"] is True
    assert json.loads(preview.calls.last.request.content)["cron"] == "0 9 * * MON"


def test_workflows_create_rejects_a_malformed_input():
    result = runner.invoke(app, ["workflows", "create", "tpl1", "--name", "x", "--input", "nokey"])
    assert result.exit_code == 2
    assert "KEY=VALUE" in result.output


@respx.mock
def test_workflows_run_waits_until_the_run_finishes(monkeypatch):
    monkeypatch.setattr("beakr_sdk.client.time.sleep", lambda _: None)
    start = respx.post(f"{API}/v1/workflows/configs/w1/run").respond(
        200, json={"id": "run1", "status": "running", "job_id": "run1"}
    )
    # Waits through the shared job endpoint (W1.2.7), then reads the run once.
    job = respx.get(f"{API}/v1/jobs/run1").mock(
        side_effect=[
            httpx.Response(200, json={"job_id": "run1", "status": "running"}),
            httpx.Response(200, json={"job_id": "run1", "status": "succeeded"}),
        ]
    )
    respx.get(f"{API}/v1/workflows/runs/run1").respond(
        200, json={"id": "run1", "status": "completed", "output_canvas_id": "c1"}
    )
    result = runner.invoke(app, ["workflows", "run", "w1", "--wait", "--input", "a=b", "--json"])
    assert result.exit_code == 0, result.output
    assert job.call_count == 2
    assert json.loads(result.output)["output_canvas_id"] == "c1"
    assert json.loads(start.calls.last.request.content) == {"input_overrides": {"a": "b"}}


@respx.mock
def test_workflows_schedule_sets_and_pauses_the_cron():
    respx.post(f"{API}/v1/workflows/schedule/preview").respond(200, json={"upcoming_runs": []})
    patch = respx.patch(f"{API}/v1/workflows/configs/w1").respond(200, json={"id": "w1"})
    result = runner.invoke(app, ["workflows", "schedule", "w1", "0 8 * * *", "--tz", "UTC"])
    assert result.exit_code == 0, result.output
    body = json.loads(patch.calls.last.request.content)
    assert body["schedule_cron"] == "0 8 * * *"
    assert body["schedule_enabled"] is True
    paused = runner.invoke(app, ["workflows", "schedule", "w1", "--off"])
    assert paused.exit_code == 0, paused.output
    assert json.loads(patch.calls.last.request.content) == {"schedule_enabled": False}


@respx.mock
def test_workflows_runs_lists_one_workflows_runs():
    route = respx.get(f"{API}/v1/workflows/configs/w1/runs").respond(
        200, json=[{"id": "run1", "status": "completed"}]
    )
    result = runner.invoke(app, ["workflows", "runs", "w1", "--status", "completed", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)[0]["id"] == "run1"
    assert route.calls.last.request.url.params["status"] == "completed"


# -- outputs -------------------------------------------------------------------


@respx.mock
def test_outputs_ls_lists_thread_outputs(rendered):
    route = respx.get(f"{API}/v1/artifacts").respond(
        200, json={"data": [{"id": "c1", "title": "Q3 memo"}], "has_more": False}
    )
    result = runner.invoke(app, ["outputs", "ls", "--project", "p1"])
    assert result.exit_code == 0, result.output
    assert "Q3 memo" in result.output
    params = route.calls.last.request.url.params
    assert params["thread_outputs"] == "true"
    assert params["project_id"] == "p1"


@respx.mock
def test_outputs_show_prints_a_revision(rendered):
    respx.get(f"{API}/v1/outputs/c1").respond(
        200, json={"title": "Q3 memo", "revision": 4, "canvas_type": "doc", "content": "now"}
    )
    respx.get(f"{API}/v1/outputs/c1/revisions/2/content").respond(
        200, json={"content": "# Earlier draft"}
    )
    result = runner.invoke(app, ["outputs", "show", "c1", "--revision", "2"])
    assert result.exit_code == 0, result.output
    assert "revision 2" in result.output
    assert "Earlier draft" in result.output


@respx.mock
def test_outputs_show_says_when_it_was_generated_and_what_was_read(rendered):
    """An output's "as of" and the source versions its agent read (SPEC-338)."""
    respx.get(f"{API}/v1/outputs/c1").respond(
        200,
        json={
            "title": "Q3 memo",
            "revision": 4,
            "canvas_type": "doc",
            "content": "now",
            "generated_at": "2026-10-10T12:00:00+00:00",
            "sources_read_total": 2,
            "sources_read": [
                {
                    "citation_key": "wiki:p1",
                    "name": "Pump P-7",
                    "cited": True,
                    "page_revision": 3,
                    "version_basis": "current_at_generation",
                },
                {
                    "citation_key": "rag:a1",
                    "name": "Calibration.pdf",
                    "cited": False,
                    "artifact_version_id": "0a1b2c3d-0000-4000-8000-000000000000",
                    "version_basis": "read",
                },
            ],
        },
    )
    result = runner.invoke(app, ["outputs", "show", "c1", "--sources"])
    assert result.exit_code == 0, result.output
    assert "As of 2026-10-10T12:00:00+00:00" in result.output
    assert "2 source(s), 1 cited" in result.output
    # An inferred version is marked; a version the run recorded is not.
    assert "page revision 3 (current at generation)" in result.output
    assert "file version 0a1b2c3d" in result.output
    assert "0a1b2c3d (current at generation)" not in result.output


@respx.mock
def test_outputs_export_writes_the_file(tmp_path):
    route = respx.get(f"{API}/v1/outputs/c1/export/pdf").respond(200, content=b"%PDF-1")
    out = tmp_path / "memo.pdf"
    result = runner.invoke(
        app, ["outputs", "export", "c1", "--format", "pdf", "--out", str(out), "--no-sources"]
    )
    assert result.exit_code == 0, result.output
    assert out.read_bytes() == b"%PDF-1"
    assert route.calls.last.request.url.params["sources"] == "none"


def test_outputs_export_rejects_an_unknown_format():
    result = runner.invoke(app, ["outputs", "export", "c1", "--format", "rtf"])
    assert result.exit_code == 2


@respx.mock
def test_outputs_history_lists_revisions(rendered):
    respx.get(f"{API}/v1/outputs/c1/revisions").respond(
        200,
        json=[{"revision_no": 3, "actor_name": "Ada", "summary": "Tightened intro"}],
    )
    result = runner.invoke(app, ["outputs", "history", "c1"])
    assert result.exit_code == 0, result.output
    assert "Tightened intro" in result.output


# -- interviews ----------------------------------------------------------------


@respx.mock
def test_interviews_request_creates_the_interview_and_prints_the_link(rendered, monkeypatch):
    monkeypatch.setattr("beakr_cli.commands.interviews.resolve_app_url", lambda: "https://app.test")
    sendable = respx.post(f"{API}/v1/interview/sendables").respond(
        201, json={"id": "s1", "title": "Assay handover"}
    )
    link = respx.post(f"{API}/v1/interview/sendables/s1/links").respond(
        201, json={"link_id": "l1", "path": "/i/tok123", "token": "tok123"}
    )
    result = runner.invoke(
        app,
        [
            "interviews", "request", "Assay handover", "--goal", "How it is run",
            "--question", "Controls?", "--for", "Ada",
        ],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    assert "https://app.test/i/tok123" in result.output
    assert json.loads(sendable.calls.last.request.content) == {
        "title": "Assay handover",
        "goals": "How it is run",
        "key_questions": ["Controls?"],
    }
    assert json.loads(link.calls.last.request.content) == {"label": "Ada"}


_MEMBER = "6f1c2a3b-4d5e-4f60-8a7b-9c0d1e2f3a4b"


@respx.mock
def test_interviews_request_to_an_email_has_beakr_email_the_link(rendered):
    sent = respx.post(f"{API}/v1/interview/requests").respond(
        201,
        json={
            "delivery": "email",
            "recipient_email": "ada@example.org",
            "sendable_id": "s1",
            "link_id": "l1",
            "path": "/i/tok123",
            "email_status": "queued",
        },
    )
    result = runner.invoke(
        app,
        [
            "interviews", "request", "Assay handover", "--to", "ada@example.org",
            "-q", "Controls?", "--due", "2026-11-01", "-m", "Thanks!",
        ],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    assert "Emailed ada@example.org" in result.output
    assert "beakr interviews show --link l1" in result.output
    request = sent.calls.last.request
    assert json.loads(request.content) == {
        "recipient": {"email": "ada@example.org"},
        "title": "Assay handover",
        "questions": ["Controls?"],
        "due": "2026-11-01",
        "message": "Thanks!",
    }
    # A create the engine replays: the SDK keys it so a retry cannot ask twice.
    assert request.headers.get("Idempotency-Key")


@respx.mock
def test_interviews_request_to_a_teammate_lands_in_their_inbox(rendered):
    sent = respx.post(f"{API}/v1/interview/requests").respond(
        201, json={"delivery": "inbox", "request_id": "q1", "recipient_user_id": _MEMBER}
    )
    result = runner.invoke(
        app,
        ["interviews", "request", "Pipeline", "--to", _MEMBER, "-P", "p1", "-g", "How it runs"],
    )
    assert result.exit_code == 0, result.output
    output = " ".join(result.output.split())  # the console wraps long lines
    assert "in their interview inbox (request q1)" in output
    assert json.loads(sent.calls.last.request.content) == {
        "recipient": {"user_id": _MEMBER},
        "project_id": "p1",
        "title": "Pipeline",
        "goals": "How it runs",
    }


@respx.mock
def test_interviews_request_in_a_sandbox_prints_the_link_to_send_yourself(rendered, monkeypatch):
    monkeypatch.setattr("beakr_cli.commands.interviews.resolve_app_url", lambda: "https://app.test")
    respx.post(f"{API}/v1/interview/requests").respond(
        201,
        json={
            "delivery": "email",
            "link_id": "l1",
            "path": "/i/tok123",
            "email_status": "not_sent_sandbox",
        },
    )
    result = runner.invoke(
        app, ["interviews", "request", "Assay", "--to", "ada@example.org", "-g", "x"]
    )
    assert result.exit_code == 0, result.output
    assert "sandbox" in result.output
    assert "https://app.test/i/tok123" in result.output


@pytest.mark.parametrize(
    "args",
    [
        ["--to", "Ada Lovelace"],
        ["--to", _MEMBER],
        ["--to", "ada@example.org", "-P", "p1"],
        ["--to", "ada@example.org", "--for", "Ada"],
        ["--due", "2026-11-01"],
    ],
    ids=[
        "not-email-or-id",
        "member-without-project",
        "email-with-project",
        "for-with-to",
        "due-without-to",
    ],
)
@respx.mock
def test_interviews_request_refuses_contradictory_options(args):
    route = respx.post(f"{API}/v1/interview/requests")
    result = runner.invoke(app, ["interviews", "request", "Assay", "-g", "x", *args])
    assert result.exit_code == 2, result.output
    assert not route.called


@respx.mock
def test_interviews_request_reports_a_refusal(rendered):
    respx.post(f"{API}/v1/interview/requests").respond(
        422,
        json={
            "detail": {
                "code": "recipient_not_project_member",
                "message": "That person is not a member of this project.",
            },
            "error": {
                "code": "recipient_not_project_member",
                "message": "That person is not a member of this project.",
                "details": {},
            },
        },
    )
    result = runner.invoke(
        app, ["interviews", "request", "Assay", "--to", _MEMBER, "-P", "p1", "-g", "x"]
    )
    assert result.exit_code != 0
    assert "not a member of this project" in " ".join(result.output.split())


@respx.mock
def test_interviews_ls_shows_sent_sessions_and_requests(rendered):
    respx.get(f"{API}/v1/interview/sendables").respond(
        200, json={"data": [{"id": "s1", "title": "Assay handover", "links": [{}]}]}
    )
    respx.get(f"{API}/v1/interview/sessions").respond(
        200, json={"data": [{"session_id": "x1", "title": "My onboarding"}], "has_more": False}
    )
    respx.get(f"{API}/v1/interview/requests").respond(
        200, json={"data": [{"id": "q1", "title": "Explain the pipeline"}], "has_more": False}
    )
    result = runner.invoke(app, ["interviews", "ls"])
    assert result.exit_code == 0, result.output
    for text in ("Assay handover", "My onboarding", "Explain the pipeline"):
        assert text in result.output


@respx.mock
def test_interviews_show_renders_a_session_and_a_link_result(rendered):
    respx.get(f"{API}/v1/interview/sessions/x1").respond(
        200,
        json={
            "title": "My onboarding",
            "status": "completed",
            "proposal_count": 2,
            "prior_turns": [
                {"role": "assistant", "text": "What do you run?"},
                {"role": "user", "text": "Binding assays."},
            ],
        },
    )
    respx.get(f"{API}/v1/interview/sendables/links/l1/result").respond(
        200, json={"status": "completed", "summary": "Controls are run daily.", "uploads": []}
    )
    session = runner.invoke(app, ["interviews", "show", "x1"])
    assert session.exit_code == 0, session.output
    assert "Q: What do you run?" in session.output
    assert "A: Binding assays." in session.output
    linked = runner.invoke(app, ["interviews", "show", "--link", "l1"])
    assert linked.exit_code == 0, linked.output
    assert "Controls are run daily." in linked.output


def test_interviews_show_needs_exactly_one_target():
    result = runner.invoke(app, ["interviews", "show"])
    assert result.exit_code == 2
