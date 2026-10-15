"""``beakr projects status|retry`` and ``beakr upload --wait-knowledge``.

Root cause this guards: an upload (even with ``--wait``) returned once files were
indexed, while Knowledge compiled for minutes afterwards, and nothing said when
it finished or that a file had failed to reach it. ``--wait-knowledge`` waits
for the project's Knowledge with progress lines and exits 1 when an uploaded
file failed; ``projects status`` says why and ``projects retry`` sends it again.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.main import app
from tests.upload_routes import mock_resumable_uploads

API = "https://api.test"
PROJECT = "4dd25154-0d80-44ab-b4c2-167319bfacb4"
STATUS = f"{API}/v1/projects/{PROJECT}/knowledge-summary"

runner = CliRunner()


@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")
    # The waits poll; never sleep for real.
    monkeypatch.setattr("beakr_sdk.client.time.sleep", lambda _seconds: None)


def _status(*, queued=0, running=0, failed=()):
    return httpx.Response(
        200,
        json={
            "total": 2,
            "eligible": len(failed),
            "syncing": queued + running,
            "synced": 2 - len(failed) - queued - running,
            "knowledge_skipped": 0,
            "oldest_pending_at": None,
            "queued": queued,
            "running": running,
            "failed": len(failed),
            "ready": queued + running == 0,
            "failed_files": [
                {
                    "artifact_id": artifact_id,
                    "filename": artifact_id,
                    "path": f"docs/{artifact_id}",
                    "error_code": "processing_failed",
                    "reason": "The file could not be read.",
                    "failed_at": "2026-10-14T09:00:00Z",
                    "retryable": True,
                }
                for artifact_id in failed
            ],
            "failed_files_next_cursor": None,
        },
    )


def _upload(tmp_path, *extra):
    mock_resumable_uploads(API)
    respx.get(url__regex=rf"{API}/v1/jobs/.+").respond(
        json={"job_id": "j", "kind": "artifact_index", "status": "succeeded"}
    )
    path = tmp_path / "notes.md"
    path.write_text("hello")
    return runner.invoke(app, ["upload", str(path), "-P", PROJECT, "--wait-knowledge", *extra])


@respx.mock
def test_wait_knowledge_prints_progress_and_succeeds_when_ready(tmp_path):
    reads = respx.get(STATUS).mock(
        side_effect=[_status(queued=1), _status(running=1), _status(running=1), _status()]
    )
    result = _upload(tmp_path)
    assert result.exit_code == 0, result.output
    assert reads.call_count == 4
    progress = [line for line in result.stderr.splitlines() if line.startswith("Knowledge:")]
    # One line per change, not per poll.
    assert progress == [
        "Knowledge: 1 queued, 0 running, 0 failed",
        "Knowledge: 0 queued, 1 running, 0 failed",
        "Knowledge: 0 queued, 0 running, 0 failed",
    ]
    assert "knowledge=ready" in result.stdout


#: What GET /v1/jobs answers for an upload whose bytes were already in the project:
#: indexing kept the existing file and rejected this one as a duplicate.
KEPT = "art-kept"
DUPLICATE_JOB = {
    "job_id": "j",
    "kind": "artifact_index",
    "status": "failed",
    "error": {
        "code": "duplicate",
        "message": "This file has already been uploaded",
        "duplicate_of": {"artifact_id": KEPT, "title": "kept.md"},
    },
}


def _upload_duplicate(tmp_path, *args):
    mock_resumable_uploads(API)
    respx.get(url__regex=rf"{API}/v1/jobs/.+").respond(json=DUPLICATE_JOB)
    path = tmp_path / "notes.md"
    path.write_text("hello")
    return runner.invoke(app, ["upload", str(path), "-P", PROJECT, *args])


@respx.mock
def test_a_duplicate_upload_is_already_uploaded_and_waits_for_the_kept_file(tmp_path):
    # Indexing rejected the re-upload and kept the existing file; the upload did
    # what was asked, so it succeeds once the kept file's Knowledge is ready.
    reads = respx.get(STATUS).mock(side_effect=[_status(running=1), _status()])
    result = _upload_duplicate(tmp_path, "--wait-knowledge")
    assert result.exit_code == 0, result.output
    assert reads.call_count == 2
    assert "Already uploaded: " in result.stderr and f"({KEPT})" in result.stderr
    assert f"duplicate_of={KEPT}" in result.stdout
    assert "knowledge=ready" in result.stdout


@respx.mock
def test_a_duplicate_upload_reports_the_kept_files_knowledge_failure(tmp_path):
    respx.get(STATUS).mock(return_value=_status(failed=(KEPT,)))
    result = _upload_duplicate(tmp_path, "--wait-knowledge", "--json")
    assert result.exit_code == 1
    [row] = json.loads(result.stdout)
    assert row["duplicate_of"]["artifact_id"] == KEPT
    assert row["knowledge"]["status"] == "failed"
    assert f"Failed: docs/{KEPT}" in result.stderr


@respx.mock
def test_wait_alone_exits_0_for_a_duplicate_upload(tmp_path):
    result = _upload_duplicate(tmp_path, "--wait")
    assert result.exit_code == 0, result.output
    assert f"duplicate_of={KEPT}" in result.stdout
    assert "Already uploaded: " in result.stderr


@respx.mock
def test_a_real_job_failure_still_exits_1(tmp_path):
    mock_resumable_uploads(API)
    respx.get(url__regex=rf"{API}/v1/jobs/.+").respond(
        json={"job_id": "j", "status": "failed", "error": {"code": "index_failed"}}
    )
    path = tmp_path / "notes.md"
    path.write_text("hello")
    result = runner.invoke(app, ["upload", str(path), "-P", PROJECT, "--wait"])
    assert result.exit_code == 1


@respx.mock
def test_wait_knowledge_exits_1_naming_the_uploaded_file_that_failed(tmp_path):
    # Another file of the project failed too; only the uploaded one is reported.
    respx.get(STATUS).mock(return_value=_status(failed=("art-notes.md", "other-file")))
    result = _upload(tmp_path, "--json")
    assert result.exit_code == 1
    [row] = json.loads(result.stdout)
    assert row["knowledge"]["status"] == "failed"
    assert row["knowledge"]["error_code"] == "processing_failed"
    assert "Failed: docs/art-notes.md  processing_failed" in result.stderr
    assert "other-file" not in result.stderr


@respx.mock
def test_wait_knowledge_gives_up_at_the_timeout_with_exit_1(tmp_path):
    respx.get(STATUS).mock(return_value=_status(running=2))
    result = _upload(tmp_path, "--timeout", "1")
    assert result.exit_code == 1
    assert "Knowledge is still compiling (2 running, 0 queued)" in result.stderr


def test_wait_knowledge_and_no_knowledge_contradict(tmp_path):
    path = tmp_path / "notes.md"
    path.write_text("hello")
    result = runner.invoke(
        app, ["upload", str(path), "-P", PROJECT, "--wait-knowledge", "--no-knowledge"]
    )
    assert result.exit_code == 2


@respx.mock
def test_status_lists_failures_and_names_the_retry():
    respx.get(STATUS).mock(return_value=_status(failed=("a1",)))
    result = runner.invoke(app, ["projects", "status", "-P", PROJECT])
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        "ready\tqueued=0\trunning=0\tfailed=1",
        "failed\ta1\tdocs/a1\tprocessing_failed",
        f"next: beakr projects retry -P {PROJECT}",
    ]


@respx.mock
def test_retry_sends_the_named_files_and_exits_1_for_one_it_could_not_retry():
    route = respx.post(f"{API}/v1/projects/{PROJECT}/knowledge/retry-failed").respond(
        json={
            "queued": 1,
            "already_queued": 0,
            "not_failed": 0,
            "not_found": 1,
            "skipped": 0,
            "files": [
                {"artifact_id": "a1", "outcome": "queued", "reason": None},
                {"artifact_id": "zz", "outcome": "not_found", "reason": None},
            ],
            "has_more": False,
        }
    )
    result = runner.invoke(
        app, ["projects", "retry", "-P", PROJECT, "--file", "a1", "--file", "zz"]
    )
    assert json.loads(route.calls[0].request.content) == {"artifact_ids": ["a1", "zz"]}
    assert result.exit_code == 1
    assert "zz\tnot_found" in result.stdout
