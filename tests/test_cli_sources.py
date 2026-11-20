"""``beakr sources``: attach connected items to a project by link or id, and manage them.

Octant asked to script project setup (2026-11-20): attach a Drive folder, Slack
channel or Notion page or database to a project, turn Knowledge on or off, start
a sync, and list or remove sources. Until then a source could only be attached
in the web app. These tests pin what is sent for each command (mocked engine
routes, shapes from ``beakr/api/routes/external_sources.py``), how a link becomes
an item, how the connection is chosen, and how the engine's refusal of an
agent's credential reads.
"""

from __future__ import annotations

import json

import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.main import app
from beakr_cli.source_links import SourceLinkError, parse_source_link, provider_slug

API = "https://api.test"
PROJECT = "p1"
SOURCES = f"{API}/v1/projects/{PROJECT}/sources"
NOTION_ID = "0123456789abcdef0123456789abcdef"
NOTION_DASHED = "01234567-89ab-cdef-0123-456789abcdef"
VIEW_ID = "fedcba9876543210fedcba9876543210"
runner = CliRunner()


@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


def _body(route) -> dict:
    return json.loads(route.calls.last.request.content)


def _attachment(**fields) -> dict:
    return {
        "id": "s1",
        "target_type": "project",
        "target_id": PROJECT,
        "provider": "notion:nango",
        "connector_id": "c1",
        "external_item_id": NOTION_DASHED,
        "external_item_type": "page",
        "external_item_name": "Assay Handbook",
        "attached_by": "u1",
        "attached_at": "2026-11-20T00:00:00Z",
        "ingest_wiki": False,
        "auto_sync_wiki": False,
        "scope_mode": "item",
        **fields,
    }


def _connections(*rows: dict) -> respx.Route:
    return respx.get(f"{API}/v1/integrations").respond(
        200, json={"data": list(rows), "has_more": False, "next_cursor": None}
    )


# -- links ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "item_id", "item_type", "name"),
    [
        (
            f"https://www.notion.so/acme/Assay-Handbook-{NOTION_ID}",
            NOTION_DASHED,
            "page",
            "Assay Handbook",
        ),
        (f"https://www.notion.so/{NOTION_ID}", NOTION_DASHED, "page", None),
        (f"https://www.notion.so/acme/{NOTION_ID.upper()}?pvs=4", NOTION_DASHED, "page", None),
        (f"https://acme.notion.site/{NOTION_DASHED}", NOTION_DASHED, "page", None),
        (
            f"https://www.notion.so/acme/Runs-{NOTION_ID}?v={VIEW_ID}",
            NOTION_DASHED,
            "database",
            "Runs",
        ),
        # A page open in peek view over a database: the page is the item.
        (
            f"https://www.notion.so/acme/{VIEW_ID}?v={VIEW_ID}&p={NOTION_ID}&pm=s",
            NOTION_DASHED,
            "page",
            None,
        ),
    ],
)
def test_a_notion_link_names_its_page_or_database(url, item_id, item_type, name):
    link = parse_source_link(url)
    assert (link.provider, link.item_id, link.item_type, link.name) == (
        "notion:nango",
        item_id,
        item_type,
        name,
    )


@pytest.mark.parametrize(
    ("url", "item_type"),
    [
        ("https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOp?usp=sharing", "folder"),
        ("https://drive.google.com/drive/u/1/folders/1AbCdEfGhIjKlMnOp", "folder"),
        ("https://drive.google.com/file/d/1AbCdEfGhIjKlMnOp/view?usp=drive_link", "file"),
        ("https://docs.google.com/document/d/1AbCdEfGhIjKlMnOp/edit", "file"),
        ("https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOp/edit#gid=0", "file"),
        ("https://drive.google.com/d/1AbCdEfGhIjKlMnOp", "file"),
        ("https://drive.google.com/open?id=1AbCdEfGhIjKlMnOp", None),
    ],
)
def test_a_drive_link_names_its_file_or_folder(url, item_type):
    link = parse_source_link(url)
    assert (link.provider, link.item_id, link.item_type) == (
        "gdrive:nango",
        "1AbCdEfGhIjKlMnOp",
        item_type,
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://acme.slack.com/archives/C01ABCDEF",
        "https://acme.slack.com/archives/C01ABCDEF/p1700000000000100",
        "https://app.slack.com/client/T0123ABCD/C01ABCDEF",
    ],
)
def test_a_slack_link_names_its_channel(url):
    link = parse_source_link(url)
    assert (link.provider, link.item_id, link.item_type) == ("slack:nango", "C01ABCDEF", "channel")


@pytest.mark.parametrize(
    "url",
    [
        "https://www.notion.so/acme/No-Id-Here",
        "https://drive.google.com/drive/my-drive",
        "https://acme.slack.com/messages",
        "https://example.com/folder/1",
        "drive.google.com/drive/folders/1AbCdEfGhIjKlMnOp",
    ],
)
def test_a_link_that_names_no_item_is_refused(url):
    with pytest.raises(SourceLinkError):
        parse_source_link(url)


def test_provider_names_people_type_become_attachment_providers():
    assert provider_slug("drive") == "gdrive:nango"
    assert provider_slug("Google-Drive") == "gdrive:nango"
    assert provider_slug("notion") == "notion:nango"
    assert provider_slug("slack") == "slack:nango"
    assert provider_slug("gdrive:nango") == "gdrive:nango"
    assert provider_slug("granola") == "granola"


# -- attach ----------------------------------------------------------------------------


@respx.mock
def test_attach_by_notion_link_sends_the_item_and_its_only_connection():
    _connections({"id": "c1", "provider": "notion:nango"}, {"id": "c2", "provider": "slack:nango"})
    route = respx.post(SOURCES).respond(201, json=_attachment(ingest_wiki=True))
    result = runner.invoke(
        app,
        [
            "sources",
            "attach",
            f"https://www.notion.so/acme/Assay-Handbook-{NOTION_ID}",
            "--knowledge",
            "-P",
            PROJECT,
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert _body(route) == {
        "provider": "notion:nango",
        "connector_id": "c1",
        "external_item_id": NOTION_DASHED,
        "external_item_type": "page",
        "external_item_name": "Assay Handbook",
        "external_item_url": f"https://www.notion.so/acme/Assay-Handbook-{NOTION_ID}",
        "ingest_wiki": True,
    }
    # A retried attach must return the first attachment, not "already attached".
    assert route.calls.last.request.headers.get("idempotency-key")
    assert json.loads(result.output)["id"] == "s1"


@respx.mock
def test_attach_by_drive_url_with_sync_starts_the_sync():
    _connections({"id": "c9", "provider": "gdrive:nango"})
    attach = respx.post(SOURCES).respond(
        201, json=_attachment(provider="gdrive:nango", external_item_type="folder")
    )
    sync = respx.post(f"{SOURCES}/s1/sync").respond(
        200, json={"job_id": "j1", "status": "queued", "provider": "drive"}
    )
    result = runner.invoke(
        app,
        [
            "sources",
            "attach",
            "--url",
            "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOp",
            "--name",
            "Contracts",
            "--knowledge",
            "--no-auto-sync",
            "--sync",
            "-P",
            PROJECT,
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    body = _body(attach)
    assert body["external_item_id"] == "1AbCdEfGhIjKlMnOp"
    assert body["external_item_type"] == "folder"
    assert body["external_item_name"] == "Contracts"
    assert body["auto_sync_wiki"] is False
    assert sync.called
    assert json.loads(result.output)["sync"]["job_id"] == "j1"


@respx.mock
def test_attach_with_sync_reports_the_attachment_when_the_sync_is_refused():
    _connections({"id": "c1", "provider": "notion:nango"})
    respx.post(SOURCES).respond(201, json=_attachment())
    respx.post(f"{SOURCES}/s1/sync").respond(
        400, json={"error": {"code": "bad_request", "message": "No active Notion connection."}}
    )
    result = runner.invoke(
        app,
        ["sources", "attach", f"https://www.notion.so/{NOTION_ID}", "--sync", "-P", PROJECT],
    )
    assert result.exit_code == 1
    assert "s1" in result.stdout
    assert "Attached s1, but the sync did not start: No active Notion connection." in result.stderr


@respx.mock
def test_attach_by_id_needs_provider_and_type_and_sends_new_fields_only_when_given():
    _connections({"id": "c3", "provider": "slack:nango"})
    route = respx.post(SOURCES).respond(201, json=_attachment(provider="slack:nango"))
    result = runner.invoke(
        app,
        [
            "sources",
            "attach",
            "C01ABCDEF",
            "--provider",
            "slack",
            "--type",
            "channel",
            "--name",
            "#assays",
            "--mode",
            "agent",
            "--pull-guidance",
            "Only assay hand-offs.",
            "--compile-guidance",
            "Record who handed off to whom.",
            "-P",
            PROJECT,
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    body = _body(route)
    assert body["provider"] == "slack:nango"
    assert body["connector_id"] == "c3"
    assert body["pull_mode"] == "agent"
    assert body["pull_guidance"] == "Only assay hand-offs."
    assert body["compile_guidance"] == "Record who handed off to whom."
    assert "source_query" not in body
    assert "scope_mode" not in body


@respx.mock
def test_attach_sends_a_query_as_json():
    _connections({"id": "c1", "provider": "notion:nango"})
    route = respx.post(SOURCES).respond(201, json=_attachment())
    query = {"notion_filter": {"property": "Status", "select": {"equals": "Final"}}}
    result = runner.invoke(
        app,
        [
            "sources",
            "attach",
            f"https://www.notion.so/{NOTION_ID}?v={VIEW_ID}",
            "--query",
            json.dumps(query),
            "-P",
            PROJECT,
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    body = _body(route)
    assert body["external_item_type"] == "database"
    assert body["source_query"] == query


@respx.mock
def test_attach_with_two_connections_asks_for_one():
    _connections(
        {"id": "c1", "provider": "gdrive:nango", "label": "Work"},
        {"id": "c2", "provider": "gdrive:nango", "label": "Lab"},
    )
    route = respx.post(SOURCES)
    result = runner.invoke(
        app,
        [
            "sources",
            "attach",
            "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOp",
            "-P",
            PROJECT,
        ],
    )
    assert result.exit_code == 2
    assert "c1 (Work)" in result.output and "c2 (Lab)" in result.output
    assert "--connector" in result.output
    assert not route.called


@respx.mock
def test_attach_with_connector_does_not_list_connections():
    listing = _connections()
    route = respx.post(SOURCES).respond(201, json=_attachment(provider="gdrive:nango"))
    result = runner.invoke(
        app,
        [
            "sources",
            "attach",
            "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOp",
            "--connector",
            "c2",
            "-P",
            PROJECT,
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert _body(route)["connector_id"] == "c2"
    assert not listing.called


@respx.mock
def test_attach_without_a_connection_is_not_found():
    _connections({"id": "c2", "provider": "slack:nango"})
    result = runner.invoke(
        app,
        ["sources", "attach", f"https://www.notion.so/{NOTION_ID}", "-P", PROJECT, "--json"],
    )
    assert result.exit_code == 4
    assert "beakr integrations connect" in json.loads(result.stdout)["error"]["message"]


@respx.mock
def test_attach_everything_a_connection_reads():
    _connections({"id": "c1", "provider": "gdrive:nango"})
    route = respx.post(SOURCES).respond(201, json=_attachment(scope_mode="all"))
    result = runner.invoke(
        app,
        ["sources", "attach", "--provider", "drive", "--scope-mode", "all", "-P", PROJECT],
    )
    assert result.exit_code == 0, result.output
    body = _body(route)
    assert (body["scope_mode"], body["external_item_id"], body["external_item_type"]) == (
        "all",
        "__all__",
        "all",
    )


@pytest.mark.parametrize(
    "argv",
    [
        # Neither a link nor provider and type.
        ["sources", "attach", "C01ABCDEF"],
        # A Drive ?id= link does not say file or folder.
        ["sources", "attach", "https://drive.google.com/open?id=1AbCdEfGhIjKlMnOp"],
        # A provider that contradicts the link.
        ["sources", "attach", f"https://www.notion.so/{NOTION_ID}", "--provider", "drive"],
        # A link that is not one --url reads.
        ["sources", "attach", "--url", "https://example.com/x"],
        ["sources", "attach", f"https://www.notion.so/{NOTION_ID}", "--query", "[1]"],
        ["sources", "attach", f"https://www.notion.so/{NOTION_ID}", "--mode", "some"],
        ["sources", "attach", f"https://www.notion.so/{NOTION_ID}", "--scope-mode", "all"],
    ],
)
@respx.mock
def test_attach_refuses_what_it_cannot_send(argv):
    route = respx.post(SOURCES)
    result = runner.invoke(app, [*argv, "-P", PROJECT])
    assert result.exit_code == 2, result.output
    assert not route.called


@respx.mock
def test_an_agents_attach_refusal_says_a_person_must_run_it():
    _connections({"id": "c1", "provider": "notion:nango"})
    respx.post(SOURCES).respond(
        403,
        json={
            "error": {
                "code": "person_review_required",
                "message": "This project requires a person to accept proposals and commit "
                "Knowledge, and this key or token is not one. Nothing was applied.",
                "fix": "Ask the person to review and accept it in the Beakr web app.",
            }
        },
    )
    argv = ["sources", "attach", f"https://www.notion.so/{NOTION_ID}", "-P", PROJECT]
    result = runner.invoke(app, argv)
    assert result.exit_code == 6
    assert "outside the agent session" in result.output
    assert "Sources in the Beakr web app" in result.output

    result = runner.invoke(app, [*argv, "--json"])
    assert result.exit_code == 6
    error = json.loads(result.stdout)["error"]
    assert error["code"] == "person_review_required"
    assert "outside the agent session" in error["fix"]


# -- ls, edit, rm, sync, status ---------------------------------------------------------


@respx.mock
def test_ls_lists_the_projects_sources(monkeypatch):
    route = respx.get(SOURCES).respond(
        200,
        json={
            "data": [_attachment(ingest_wiki=True, sync_status="completed")],
            "has_more": False,
            "next_cursor": None,
        },
    )
    result = runner.invoke(app, ["sources", "ls", "-P", PROJECT])
    assert result.exit_code == 0, result.output
    assert route.calls.last.request.url.params["limit"] == "200"
    line = result.stdout.splitlines()[0].split("\t")
    assert line[:2] == ["s1", "Assay Handbook"]
    assert "knowledge=on" in line and "sync=completed" in line

    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)
    result = runner.invoke(app, ["sources", "ls", "-P", PROJECT])
    assert "Assay" in result.output and "completed" in result.output


@respx.mock
def test_edit_sends_only_what_changes_and_clears_on_request():
    route = respx.patch(f"{SOURCES}/s1").respond(200, json=_attachment(ingest_wiki=True))
    result = runner.invoke(
        app,
        ["sources", "edit", "s1", "--knowledge", "--clear-query", "-P", PROJECT, "--json"],
    )
    assert result.exit_code == 0, result.output
    assert _body(route) == {"ingest_wiki": True, "source_query": None}

    result = runner.invoke(
        app,
        [
            "sources",
            "edit",
            "s1",
            "--no-auto-sync",
            "--mode",
            "all",
            "--query",
            '{"drive_q": "mimeType = \'application/pdf\'"}',
            "--clear-pull-guidance",
            "--compile-guidance",
            "Skip drafts.",
            "-P",
            PROJECT,
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    assert _body(route) == {
        "auto_sync_wiki": False,
        "pull_mode": "all",
        "source_query": {"drive_q": "mimeType = 'application/pdf'"},
        "compile_guidance": "Skip drafts.",
        "pull_guidance": None,
    }


@pytest.mark.parametrize(
    "argv",
    [
        ["--query", '{"drive_q": "x"}', "--clear-query"],
        ["--pull-guidance", "x", "--clear-pull-guidance"],
        ["--compile-guidance", "x", "--clear-compile-guidance"],
    ],
)
def test_edit_refuses_setting_and_clearing_one_field(argv):
    result = runner.invoke(app, ["sources", "edit", "s1", *argv, "-P", PROJECT])
    assert result.exit_code == 2
    assert "not both" in result.output


def test_edit_needs_a_change():
    result = runner.invoke(app, ["sources", "edit", "s1", "-P", PROJECT])
    assert result.exit_code == 2
    assert "Nothing to change" in result.output


@respx.mock
def test_rm_detaches_with_yes_and_refuses_without_it():
    route = respx.delete(f"{SOURCES}/s1").respond(204)
    result = runner.invoke(app, ["sources", "rm", "s1", "-P", PROJECT])
    assert result.exit_code == 2
    assert not route.called
    result = runner.invoke(app, ["sources", "rm", "s1", "-y", "-P", PROJECT, "--json"])
    assert result.exit_code == 0, result.output
    assert route.called
    assert json.loads(result.stdout) == {"id": "s1", "project_id": PROJECT, "removed": True}


@respx.mock
def test_sync_starts_and_names_the_status_command():
    route = respx.post(f"{SOURCES}/s1/sync").respond(
        200, json={"job_id": "j1", "status": "queued", "provider": "notion"}
    )
    result = runner.invoke(app, ["sources", "sync", "s1", "-P", PROJECT])
    assert result.exit_code == 0, result.output
    assert route.called
    assert "next: beakr sources status s1" in result.stdout


@respx.mock
def test_status_reports_the_latest_sync(monkeypatch):
    respx.get(f"{SOURCES}/s1/sync-status").respond(
        200,
        json={
            "status": "completed",
            "files_total": 3,
            "files_completed": 2,
            "files_failed": 1,
            "files": [
                {"name": "Q3.pdf", "status": "failed", "outcome": "failed", "reason": "locked"}
            ],
        },
    )
    result = runner.invoke(app, ["sources", "status", "s1", "-P", PROJECT])
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[0].split("\t")[:2] == ["s1", "completed"]
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)
    result = runner.invoke(app, ["sources", "status", "s1", "-P", PROJECT])
    assert "2/3 files done, 1 failed" in result.output
    assert "Q3.pdf  locked" in result.output


def test_sources_commands_need_a_project(isolated_home):
    result = runner.invoke(app, ["sources", "ls"])
    assert result.exit_code == 2
    assert "beakr init" in result.output
