"""What an agent driving the CLI through a pipe gets (dogfood friction log, 2026-10-12).

Root causes these pin:

* rows 1/4 -- piped output was every object's full JSON (20+ fields per
  project), so an agent paid for fields it never read; it is now one line per
  object plus a ``next:`` hint, and ``--json`` still prints everything;
* rows 2/3 -- ``--project`` took only a UUID (``-P "Platform Docs"`` failed with
  "project_id must be a valid UUID") and ``--scope``, the MCP's word, was
  unknown; it now takes an id, slug or exact name under either flag;
* row 5 -- nothing could wait for indexing without curl; ``jobs show|wait`` and
  ``upload --wait`` do;
* row 6 -- external users and labels were SDK-only;
* kb sources/cat named a source's internal id, not where the document lives.
"""

from __future__ import annotations

import json

import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.main import app
from tests.upload_routes import mock_resumable_uploads

API = "https://api.test"
ORG = "org-1"
PROJECT_ID = "e56b7c9b-4616-4c89-94bc-6c1c49af35cc"
PROJECTS = [
    {
        "id": PROJECT_ID,
        "name": "Platform Docs",
        "slug": "platform-docs",
        "project_type": "standard",
        "status": "active",
        "description": "x" * 300,
        "settings": {"a": 1},
        "tags": [],
    },
    {"id": "3ca280e6-466e-4abd-8a56-14dbe1dfad47", "name": "My Project", "slug": "my-project"},
]
runner = CliRunner()


@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


def _projects() -> respx.Route:
    return respx.get(f"{API}/v1/projects").respond(
        json={"data": PROJECTS, "has_more": False, "next_cursor": None}
    )


# --- rows 1/4: compact piped output ------------------------------------------------


@respx.mock
def test_piped_projects_ls_is_one_line_per_project_and_json_keeps_everything(monkeypatch):
    _projects()
    piped = runner.invoke(app, ["projects", "ls"])
    assert piped.exit_code == 0, piped.output
    first = piped.stdout.splitlines()[0].split("\t")
    assert first[:3] == [PROJECT_ID, "Platform Docs", "slug=platform-docs"]
    assert "settings" not in piped.stdout and "x" * 50 not in piped.stdout

    full = runner.invoke(app, ["projects", "ls", "--json"])
    assert json.loads(full.stdout)[0]["settings"] == {"a": 1}

    monkeypatch.setenv("BEAKR_OUTPUT", "json")
    assert json.loads(runner.invoke(app, ["projects", "ls"]).stdout)[0]["id"] == PROJECT_ID


@respx.mock
def test_a_created_project_names_the_next_command():
    respx.post(f"{API}/v1/projects").respond(json=PROJECTS[0])
    result = runner.invoke(app, ["projects", "create", "Platform Docs"])
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[-1] == "next: beakr upload PATH -P platform-docs"


def test_a_one_time_secret_is_never_dropped_from_a_summary():
    from beakr_cli.compact import summary_line

    line = summary_line({"id": "k1", "name": "CI", "key": "bk_live_secret", "prefix": "bk_l"})
    assert "key=bk_live_secret" in line.split("\t")


def test_an_envelope_with_more_rows_names_the_next_page(monkeypatch):
    import click

    from beakr_cli import compact

    monkeypatch.setattr(compact.sys, "argv", ["beakr", "events", "ls", "--cursor", "c1"])
    command = click.Command("ls", params=[click.Option(["--cursor"])])
    with click.Context(command):
        hint = compact.next_page_hint({"data": [], "has_more": True, "next_cursor": "c2"})
    assert hint == "beakr events ls --cursor c2"


# --- rows 2/3: --project takes an id, slug or name; --scope is an alias ---------------


@pytest.mark.real_project_refs
@respx.mock
@pytest.mark.parametrize("ref", ["Platform Docs", "platform docs", "platform-docs", PROJECT_ID])
def test_project_option_takes_an_id_slug_or_name(ref):
    listed = _projects()
    pages = respx.get(f"{API}/v1/knowledge/wiki/pages").respond(
        json={"data": [], "has_more": False, "next_cursor": None}
    )
    result = runner.invoke(app, ["kb", "ls", "--scope", ref, "--json"])
    assert result.exit_code == 0, result.output
    assert pages.calls[0].request.url.params["project_id"] == PROJECT_ID
    # An id is used as given: no lookup request.
    assert listed.called is (ref != PROJECT_ID)


@pytest.mark.real_project_refs
@respx.mock
def test_an_unknown_project_is_a_usage_error_naming_the_closest():
    _projects()
    result = runner.invoke(app, ["kb", "ls", "-P", "Platform Doc"])
    assert result.exit_code == 2
    assert "Did you mean: Platform Docs (platform-docs" in " ".join(result.output.split())


# --- row 5: jobs show|wait and upload --wait --------------------------------------------


@respx.mock
def test_jobs_wait_reports_each_job_and_fails_when_one_failed():
    respx.get(f"{API}/v1/jobs/j1").respond(
        json={"job_id": "j1", "kind": "artifact_index", "status": "succeeded"}
    )
    respx.get(f"{API}/v1/jobs/j2").respond(
        json={
            "job_id": "j2",
            "kind": "artifact_index",
            "status": "failed",
            "error": {"code": "unreadable", "message": "PDF has no text"},
        }
    )
    result = runner.invoke(app, ["jobs", "wait", "j1", "j2"])
    assert result.exit_code == 1
    lines = result.stdout.splitlines()
    assert lines[0].split("\t") == ["j1", "kind=artifact_index", "status=succeeded"]
    assert "error=PDF has no text" in lines[1]


@respx.mock
def test_jobs_show_points_a_running_job_at_jobs_wait():
    respx.get(f"{API}/v1/jobs/j1").respond(
        json={"job_id": "j1", "kind": "artifact_index", "status": "running"}
    )
    result = runner.invoke(app, ["jobs", "show", "j1"])
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[-1] == "next: beakr jobs wait j1"


@respx.mock
def test_upload_prints_each_file_and_job_then_how_to_wait(tmp_path):
    mock_resumable_uploads(API)
    path = tmp_path / "notes.md"
    path.write_text("hello")
    result = runner.invoke(app, ["upload", str(path), "-P", "p1"])
    assert result.exit_code == 0, result.output
    first, hint = result.stdout.splitlines()
    file_id, shown_path, job = first.split("\t")
    assert file_id == "art-notes.md" and shown_path == str(path) and job.startswith("job=")
    assert hint == f"next: beakr jobs wait {job.removeprefix('job=')}"


@respx.mock
def test_upload_wait_blocks_until_indexed_and_exits_1_if_indexing_failed(tmp_path):
    mock_resumable_uploads(API)
    respx.get(url__regex=rf"{API}/v1/jobs/.+").respond(
        json={"job_id": "j", "kind": "artifact_index", "status": "failed"}
    )
    path = tmp_path / "notes.md"
    path.write_text("hello")
    result = runner.invoke(app, ["upload", str(path), "-P", "p1", "--wait"])
    assert result.exit_code == 1
    # Failed jobs: no "check Knowledge" hint.
    assert "status=failed" in result.stdout and "next:" not in result.stdout


# --- row 6: external users and labels on the published CLI -----------------------------


def _active_org():
    respx.get(f"{API}/v1/me/organization").respond(json={"id": ORG, "name": "Org"})


@respx.mock
def test_external_users_create_is_idempotent_and_names_the_label_step():
    _active_org()
    create = respx.post(f"{API}/v1/organizations/{ORG}/external-users").respond(
        json={"user_id": "u9", "external_id": "cust-42", "name": "Ada", "created": False}
    )
    result = runner.invoke(app, ["external-users", "create", "cust-42", "--name", "Ada"])
    assert result.exit_code == 0, result.output
    assert json.loads(create.calls[0].request.content) == {"external_id": "cust-42", "name": "Ada"}
    assert result.stdout.splitlines()[-1] == "next: beakr labels members add LABEL u9"


@pytest.mark.real_project_refs
@respx.mock
def test_labels_take_a_name_and_projects_a_slug():
    _projects()
    respx.get(f"{API}/v1/labels").respond(
        json={"data": [{"id": "l1", "name": "Acme customers"}], "has_more": False}
    )
    attach = respx.post(f"{API}/v1/labels/l1/projects").respond(
        json={"label_id": "l1", "project_id": PROJECT_ID, "role": "viewer", "project": {}}
    )
    result = runner.invoke(app, ["labels", "projects", "add", "acme customers", "platform-docs"])
    assert result.exit_code == 0, result.output
    assert json.loads(attach.calls[0].request.content) == {"project_id": PROJECT_ID}


# --- kb sources / kb cat: where each source lives -----------------------------------------

PAGE_ID = "33333333-3333-3333-3333-333333333333"
PAGES = f"{API}/v1/knowledge/wiki/pages"
UPLOADED = {
    "source_ref": "beakr-source:v1:1",
    "key": "artifact:v1-u1",
    "source_type": "artifact",
    "source_title": "runtime.md",
    "file_name": "runtime.md",
    "origin": "upload",
    "artifact_id": "f1",
    "artifact_version_id": "v1",
    "section_id": "sec_a",
    "stance": "support",
}
SYNCED = {
    "source_ref": "beakr-source:v1:2",
    "key": "artifact:v2-u4",
    "source_type": "artifact",
    "source_title": "Q3 plan",
    "file_name": "Q3 plan.pdf",
    "provider": "google_drive",
    "web_view_url": "https://drive.example/q3",
    "artifact_id": "f2",
    "stance": "qualifies",
}


def _page_with_sources():
    respx.get(f"{PAGES}/{PAGE_ID}").respond(
        json={"id": PAGE_ID, "title": "Runtime", "content": "# Runtime\n\nBody."}
    )
    respx.get(f"{PAGES}/{PAGE_ID}/sources").respond(
        json={"data": [UPLOADED, UPLOADED, SYNCED], "has_more": False, "next_cursor": None}
    )


@respx.mock
def test_kb_sources_says_where_each_document_lives():
    _page_with_sources()
    result = runner.invoke(app, ["kb", "sources", PAGE_ID])
    assert result.exit_code == 0, result.output
    uploaded, synced, hint = result.stdout.splitlines()
    assert uploaded.split("\t")[:3] == [
        "[1]",
        "runtime.md · upload · file f1 (`beakr files download f1`)",
        "citations=2",
    ]
    assert synced.split("\t")[1] == "Q3 plan.pdf · google_drive · https://drive.example/q3"
    assert hint.startswith(f"next: beakr kb sources {PAGE_ID} --json")

    records = json.loads(runner.invoke(app, ["kb", "sources", PAGE_ID, "--json"]).stdout)
    assert len(records) == 3
    assert records[0]["location"] == {"file_id": "f1"}
    assert records[2]["location"]["url"] == "https://drive.example/q3"
    assert "artifact_version_id" not in json.dumps(records)


@respx.mock
def test_kb_cat_prints_the_page_then_its_sources():
    _page_with_sources()
    result = runner.invoke(app, ["kb", "cat", PAGE_ID])
    assert result.exit_code == 0, result.output
    assert "# Runtime\n\nBody." in result.stdout
    assert result.stdout.rstrip().splitlines()[-1].startswith("[2]\tQ3 plan.pdf · google_drive")
    as_json = json.loads(runner.invoke(app, ["kb", "cat", PAGE_ID, "--json"]).stdout)
    assert as_json["sources"][0]["citation_key"] == "artifact:v1-u1"
