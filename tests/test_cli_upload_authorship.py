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

    author = ["--author", "Jane Doe <jane@example.com>", "--author", "Bob Smith"]
    author += ["--date", "2026-06-14"]
    result = runner.invoke(app, ["upload", str(note), str(folder), "-P", "p1", *author])
    resumed = runner.invoke(app, ["upload", str(note), "-P", "p1", "--date", "2026-06-15"])

    assert result.exit_code == 0, result.output
    assert resumed.exit_code == 0, resumed.output
    authored = [
        {key: body[key] for key in ("authored_by", "authored_at") if key in body} for body in bodies
    ]
    both = ["Jane Doe <jane@example.com>", "Bob Smith"]
    assert authored == [
        {"authored_by": both, "authored_at": "2026-06-14"},
        {"authored_by": both, "authored_at": "2026-06-14"},
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
    "authored_by": "Jane Doe and Bob Smith",
    "authored_by_user_id": "u-jane",
    "authors": [
        {"name": "Jane Doe", "user_id": "u-jane", "email": "jane@example.com"},
        {"name": "Bob Smith", "user_id": None, "email": None},
    ],
    "authored_at": "2026-06-14",
}


@respx.mock
def test_files_edit_records_and_clears_the_authors(monkeypatch):
    route = respx.patch(f"{API}/v1/artifacts/a1").respond(200, json=ARTIFACT)
    argv = ["files", "edit", "a1", "--author", "Jane Doe <jane@example.com>"]
    result = runner.invoke(app, [*argv, "--author", "Bob Smith", "--date", "2026-06-14"])
    assert result.exit_code == 0, result.output
    assert json.loads(route.calls.last.request.content) == {
        "authored_by": ["Jane Doe <jane@example.com>", "Bob Smith"],
        "authored_at": "2026-06-14",
    }
    assert result.stdout.splitlines() == [
        "a1\tauthored_by=Jane Doe and Bob Smith\tauthored_at=2026-06-14\tauthors=2"
    ]
    one = runner.invoke(app, ["files", "edit", "a1", "--author", "Jane Doe"])
    assert one.exit_code == 0, one.output
    assert json.loads(route.calls.last.request.content) == {"authored_by": "Jane Doe"}
    route.respond(200, json={**ARTIFACT, "authored_by": None, "authors": []})
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
    assert "Authors:   Jane Doe <jane@example.com>; Bob Smith" in result.output
    assert "Written:   2026-06-14" in result.output


@respx.mock
def test_files_show_piped_says_who_wrote_it_and_when():
    """What an agent reads (compact, piped) names the authors and the date, as the
    terminal view does; a file with neither keeps the one summary line."""
    route = respx.get(f"{API}/v1/artifacts/a1").respond(200, json=ARTIFACT)
    result = runner.invoke(app, ["files", "show", "a1"])
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        "a1\tAssay notes\tauthors=Jane Doe <jane@example.com>; Bob Smith\tauthored_at=2026-06-14"
    ]
    route.respond(200, json={**ARTIFACT, "authored_by": None, "authors": [], "authored_at": None})
    bare = runner.invoke(app, ["files", "show", "a1"])
    assert bare.exit_code == 0, bare.output
    assert bare.stdout.splitlines() == ["a1\tAssay notes"]


def test_the_stdio_server_keeps_who_wrote_a_cited_file():
    """As the hosted ``formatting.public_source`` keeps it (engine fix/upload-authors-list)."""
    from beakr_cli import mcp_server

    written = {
        "authors": [
            {"name": "Jane Doe", "org_member": True, "user_id": "u-jane"},
            {"name": "Bob Smith", "org_member": False},
        ],
        "by_agent": True,
        "text": "an agent for Jane Doe and Bob Smith",
    }
    item = mcp_server._public_source(
        {"source_ref": "file:a1", "title": "Assay notes", "written_by": written}
    )
    assert item["written_by"] == {
        "by_agent": True,
        "text": "an agent for Jane Doe and Bob Smith",
        "authors": [
            {"name": "Jane Doe", "org_member": True},
            {"name": "Bob Smith", "org_member": False},
        ],
    }
    bare = mcp_server._public_source({"source_ref": "file:a1", "written_by": {"authors": []}})
    assert "written_by" not in bare


# r108 sweep (sandbox, 26 Nov 2026): 21 authors went to the engine, which answered
# "Input should be a valid string" (the first branch of its validation error) and,
# for a folder, refused each file in turn. The MCP server's upload_files already
# checked them with the API's own parsers before sending; the CLI now does too.
TWENTY_ONE = [arg for n in range(1, 22) for arg in ("--author", f"Author {n}")]


@pytest.mark.parametrize(
    ("argv", "flag", "said"),
    [
        (TWENTY_ONE, "--author", "authored_by names more than 20 authors. Nothing was sent."),
        (["--author", "Bad <not-an-email>"], "--author", "'not-an-email' is not an email"),
        (["--date", "2999-01-01"], "--date", "'2999-01-01' is in the future. Nothing was sent."),
        (["--date", "yesterday"], "--date", "'yesterday' is not a date (YYYY-MM-DD)"),
    ],
    ids=["21-authors", "bad-email", "future-date", "not-a-date"],
)
@respx.mock(assert_all_called=False)
def test_a_bad_author_or_date_is_refused_before_anything_is_sent(tmp_path, argv, flag, said):
    routes = mock_resumable_uploads(API)
    edit = respx.patch(f"{API}/v1/artifacts/a1").respond(200, json=ARTIFACT)
    note = tmp_path / "note.md"
    note.write_text("x")
    for command in (["upload", str(note), "-P", "p1"], ["files", "edit", "a1"]):
        result = runner.invoke(app, [*command, *argv])
        assert result.exit_code == 2, result.output
        said_once = " ".join(result.output.replace("│", " ").split())
        assert flag in said_once and said in said_once
    assert not edit.called
    assert not routes["create"].called and not routes["complete"].called


@respx.mock
def test_twenty_authors_and_a_clear_still_go_through():
    route = respx.patch(f"{API}/v1/artifacts/a1").respond(200, json=ARTIFACT)
    twenty = [arg for n in range(1, 21) for arg in ("--author", f"Author {n}")]
    assert runner.invoke(app, ["files", "edit", "a1", *twenty]).exit_code == 0
    assert len(json.loads(route.calls.last.request.content)["authored_by"]) == 20
    cleared = runner.invoke(app, ["files", "edit", "a1", "--author", "", "--date", ""])
    assert cleared.exit_code == 0, cleared.output
    assert json.loads(route.calls.last.request.content) == {"authored_by": "", "authored_at": ""}


VERSIONS = {
    # The route lists a file's versions oldest first.
    "data": [
        {
            "id": "v-one",
            "version_no": 1,
            "created_at": "2026-11-26T20:47:53Z",
            "size_bytes": 49,
            "mime_type": "text/plain",
            "written_by_agent": False,
        },
        {
            "id": "v-two",
            "version_no": 2,
            "created_at": "2026-11-26T20:48:50Z",
            "size_bytes": 71,
            "mime_type": "text/plain",
            "written_by_agent": True,
        },
    ],
    "has_more": False,
    "next_cursor": None,
}


@respx.mock
def test_files_versions_piped_is_newest_first_with_each_versions_number():
    """r108 sweep: piped, `files versions` printed only the ids in the route's order,
    oldest first, though it says "newest first", so an agent could not tell which
    id was the newest version."""
    respx.get(f"{API}/v1/artifacts/a1/versions").respond(200, json=VERSIONS)
    result = runner.invoke(app, ["files", "versions", "a1"])
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        "v-two\tversion=2\tcreated_at=2026-11-26T20:48:50Z\tsize_bytes=71"
        "\tmime_type=text/plain\twritten_by_agent=true",
        "v-one\tversion=1\tcreated_at=2026-11-26T20:47:53Z\tsize_bytes=49\tmime_type=text/plain",
    ]
