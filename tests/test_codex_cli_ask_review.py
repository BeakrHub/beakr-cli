"""CLI streaming must retain failure status and citations across event boundaries."""

import json

import httpx
import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.main import app


@pytest.fixture(autouse=True)
def credential(monkeypatch):
    monkeypatch.setenv("BEAKR_BASE_URL", "https://api.test")
    monkeypatch.setenv("BEAKR_API_KEY", "bk_test_fixture")


def run_ask(events, *, json_out=False):
    stream = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
    with respx.mock() as router:
        router.post("https://api.test/v1/runs").respond(
            200, json={"run_id": "r1", "thread_id": "t1"}
        )
        router.get("https://api.test/v1/runs/r1/events").mock(
            return_value=httpx.Response(
                200, text=stream, headers={"content-type": "text/event-stream"}
            )
        )
        return CliRunner().invoke(app, ["ask", "question", *(["--json"] if json_out else [])])


@pytest.mark.parametrize("json_out", [False, True])
@pytest.mark.parametrize("status", ["failed", "cancelled", "error-event"])
def test_ask_failure_status_is_independent_of_output_format(json_out, status):
    events = []
    if status == "error-event":
        events.append({"type": "error", "data": {"message": "upstream failure"}})
    events.append(
        {
            "type": "stream_complete",
            "data": {"status": "completed" if status == "error-event" else status},
        }
    )
    result = run_ask(events, json_out=json_out)
    assert result.exit_code == 1, result.output
    if json_out:
        assert json.loads(result.stdout.splitlines()[-1])["type"] == "stream_complete"


@pytest.mark.parametrize("chunks", [["Use {{doc:", "a1}}."], ["Use {", "{doc:a1}", "}."]])
def test_ask_citation_survives_split_content_deltas(chunks):
    events = [{"type": "content_delta", "data": {"delta": chunk}} for chunk in chunks]
    events += [
        {"type": "sources", "data": {"sources": [{"key": "doc:a1", "title": "SOP"}]}},
        {"type": "stream_complete", "data": {"status": "completed"}},
    ]
    result = run_ask(events)
    assert result.exit_code == 0, result.output
    assert "Use [1]." in result.stdout
    assert "[1] SOP" in result.stdout
    assert "{{doc:" not in result.stdout
