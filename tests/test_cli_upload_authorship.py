"""``beakr upload --author --date`` and ``beakr files edit``: who wrote a file, and when.

Octant R11: an upload could not carry its original author and date, so Knowledge
attributed what a file stated to the uploader and dated it by the upload. The
engine (fix/upload-authored-by) takes ``authored_by`` and ``authored_at`` on
``POST /v1/uploads/{id}/complete`` and ``PATCH /v1/artifacts/{id}``; these tests
pin what the CLI sends with the locked beakr-sdk (0.2.0, which sends neither) and
how the answers read.
"""

from __future__ import annotations

import json

import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.main import app
from tests.test_cli_files import _completions
from tests.upload_routes import mock_resumable_uploads

API = "https://api.test"
runner = CliRunner()


@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


@respx.mock
def test_author_and_date_reach_every_completion(tmp_path):
    routes = mock_resumable_uploads(API)
    bodies = _completions(routes)
    note = tmp_path / "note.md"
    note.write_text("x")
    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "a.md").write_text("a")

    author = ["--author", "Jane Doe <jane@example.com>", "--date", "2026-06-14"]
    result = runner.invoke(app, ["upload", str(note), str(folder), "-P", "p1", *author])
    resumed = runner.invoke(app, ["upload", str(note), "-P", "p1", "--date", "2026-06-15"])

    assert result.exit_code == 0, result.output
    assert resumed.exit_code == 0, resumed.output
    authored = [
        {key: body[key] for key in ("authored_by", "authored_at") if key in body} for body in bodies
    ]
    assert authored == [
        {"authored_by": "Jane Doe <jane@example.com>", "authored_at": "2026-06-14"},
        {"authored_by": "Jane Doe <jane@example.com>", "authored_at": "2026-06-14"},
        {"authored_at": "2026-06-15"},
    ]


@respx.mock
def test_without_them_nothing_is_sent(tmp_path):
    routes = mock_resumable_uploads(API)
    bodies = _completions(routes)
    note = tmp_path / "note.md"
    note.write_text("x")
    assert runner.invoke(app, ["upload", str(note), "-P", "p1"]).exit_code == 0
    assert "authored_by" not in bodies[0] and "authored_at" not in bodies[0]


ARTIFACT = {
    "id": "a1",
    "title": "Assay notes",
    "authored_by": "Jane Doe <jane@example.com>",
    "authored_by_user_id": "u-jane",
    "authored_at": "2026-06-14",
}


@respx.mock
def test_files_edit_records_and_clears_the_author(monkeypatch):
    route = respx.patch(f"{API}/v1/artifacts/a1").respond(200, json=ARTIFACT)
    result = runner.invoke(
        app,
        ["files", "edit", "a1", "--author", "Jane Doe <jane@example.com>", "--date", "2026-06-14"],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(route.calls.last.request.content) == {
        "authored_by": "Jane Doe <jane@example.com>",
        "authored_at": "2026-06-14",
    }
    assert result.stdout.splitlines() == [
        "a1\tauthored_by=Jane Doe <jane@example.com>\tauthored_at=2026-06-14"
        "\tauthored_by_user_id=u-jane"
    ]
    route.respond(200, json={**ARTIFACT, "authored_by": None, "authored_by_user_id": None})
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)
    result = runner.invoke(app, ["files", "edit", "a1", "--author", ""])
    assert json.loads(route.calls.last.request.content) == {"authored_by": ""}
    assert "Saved Assay notes: no author recorded, 2026-06-14." in result.output


def test_files_edit_needs_a_field():
    result = runner.invoke(app, ["files", "edit", "a1"])
    assert result.exit_code == 2
    assert "--author" in result.output and "--date" in result.output


@respx.mock
def test_files_show_says_who_wrote_it(monkeypatch):
    respx.get(f"{API}/v1/artifacts/a1").respond(200, json=ARTIFACT)
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)
    result = runner.invoke(app, ["files", "show", "a1"])
    assert result.exit_code == 0, result.output
    assert "Author:    Jane Doe <jane@example.com>" in result.output
    assert "Written:   2026-06-14" in result.output
