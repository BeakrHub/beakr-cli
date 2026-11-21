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


#: ``GET /v1/integrations/source-query-keys`` as the engine answers it (#3062,
#: ``integration_rules.validation.source_query_capabilities``), a few keys of it.
QUERY_KEYS = [
    {
        "key": "drive_q",
        "providers": ["gdrive:nango"],
        "item_types": None,
        "excluded_item_types": ["file"],
        "kind": "text",
        "fields": None,
        "max_chars": 2000,
        "note": None,
        "example": "mimeType = 'application/pdf'",
    },
    {
        "key": "notion_filter",
        "providers": ["notion:nango"],
        "item_types": ["database"],
        "excluded_item_types": None,
        "kind": "object",
        "fields": None,
        "max_chars": None,
        "note": None,
        "example": {"property": "Status", "select": {"equals": "Done"}},
    },
    {
        "key": "notion_view",
        "providers": ["notion:nango"],
        "item_types": ["database"],
        "excluded_item_types": None,
        "kind": "text",
        "fields": None,
        "max_chars": None,
        "note": None,
        "example": "1f0a2b3c4d5e6f708192a3b4c5d6e7f8",
    },
    {
        "key": "notion_page",
        "providers": ["notion:nango"],
        "item_types": ["page"],
        "excluded_item_types": None,
        "kind": "object",
        "fields": None,
        "max_chars": None,
        "note": None,
        "example": {"default": None, "databases": {"d1": {"exclude": True}}},
    },
    {
        "key": "gmail_q",
        "providers": ["gmail:nango"],
        "item_types": None,
        "excluded_item_types": None,
        "kind": "text",
        "fields": None,
        "max_chars": 1000,
        "note": None,
        "example": "from:legal@acme.com has:attachment",
    },
    {
        "key": "jql",
        "providers": ["jira:nango"],
        "item_types": None,
        "excluded_item_types": None,
        "kind": "text",
        "fields": None,
        "max_chars": 1000,
        "note": "No ORDER BY.",
        "example": "status = Done AND labels = customer",
    },
    {
        "key": "slack_filter",
        "providers": ["slack:nango"],
        "item_types": None,
        "excluded_item_types": None,
        "kind": "structured",
        "fields": ["after", "channels"],
        "max_chars": None,
        "note": None,
        "example": {"channels": ["C0123ABCD"], "after": "2025-01-01"},
    },
]


def _keys(status: int = 200) -> respx.Route:
    route = respx.get(f"{API}/v1/integrations/source-query-keys")
    if status == 200:
        return route.respond(200, json={"keys": QUERY_KEYS})
    return route.respond(status, json={"error": {"code": "not_found", "message": "Not Found"}})


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


def test_a_notion_database_link_keeps_its_saved_view():
    link = parse_source_link(f"https://www.notion.so/acme/{NOTION_ID}?v={VIEW_ID}")
    assert link.view_id == "fedcba98-7654-3210-fedc-ba9876543210"
    assert parse_source_link(f"https://www.notion.so/{NOTION_ID}").view_id is None


@respx.mock
def test_attach_by_a_view_link_says_how_to_pull_only_that_view(monkeypatch):
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)
    _connections({"id": "c1", "provider": "notion:nango"})
    respx.post(SOURCES).respond(201, json=_attachment(external_item_type="database"))
    result = runner.invoke(
        app, ["sources", "attach", f"https://www.notion.so/{NOTION_ID}?v={VIEW_ID}", "-P", PROJECT]
    )
    assert result.exit_code == 0, result.output
    assert (
        'beakr sources edit s1 --query \'{"notion_view": "fedcba98-7654-3210-fedc-ba9876543210"}\''
    ) in result.stderr


@respx.mock
def test_attach_sends_a_notion_view_query():
    _keys()
    _connections({"id": "c1", "provider": "notion:nango"})
    route = respx.post(SOURCES).respond(201, json=_attachment(external_item_type="database"))
    query = {"notion_view": VIEW_ID}
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
    assert _body(route)["source_query"] == query


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
            "--guidance",
            "Only assay hand-offs; record who handed off to whom.",
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
    assert body["guidance"] == "Only assay hand-offs; record who handed off to whom."
    assert "source_query" not in body
    assert "scope_mode" not in body


@respx.mock
def test_attach_sends_a_query_as_json():
    _keys()
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
    _keys()
    respx.get(SOURCES).respond(
        200,
        json={
            "data": [_attachment(provider="gdrive:nango", external_item_type="folder")],
            "has_more": False,
        },
    )
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
            "--clear-guidance",
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
        "guidance": None,
    }


@pytest.mark.parametrize(
    "argv",
    [
        ["--query", '{"drive_q": "x"}', "--clear-query"],
        ["--guidance", "x", "--clear-guidance"],
    ],
)
def test_edit_refuses_setting_and_clearing_one_field(argv):
    result = runner.invoke(app, ["sources", "edit", "s1", *argv, "-P", PROJECT])
    assert result.exit_code == 2
    assert "not both" in result.output


@respx.mock
def test_a_rule_the_engine_refuses_reads_as_its_message():
    # An engine that does not list its keys: the query is sent as given.
    _keys(404)
    respx.patch(f"{SOURCES}/s1").respond(
        422,
        json={
            "detail": [
                {
                    "loc": ["body", "source_query"],
                    "msg": "Value error, source_query has unknown key(s) gmail_q",
                    "type": "value_error",
                }
            ]
        },
    )
    result = runner.invoke(
        app, ["sources", "edit", "s1", "--query", '{"gmail_q": "x"}', "-P", PROJECT]
    )
    assert result.exit_code == 1
    assert "source_query has unknown key(s) gmail_q" in result.output


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


# -- every provider's query, checked against the engine's keys ------------------------


@pytest.mark.parametrize(
    ("flag", "key"),
    [
        ("--drive-q", "drive_q"),
        ("--gmail-q", "gmail_q"),
        ("--jql", "jql"),
        ("--cql", "cql"),
        ("--github-q", "github_q"),
    ],
)
@respx.mock
def test_a_shorthand_is_the_query_with_its_key(flag, key):
    keys = [{"key": key, "providers": ["x:nango"], "kind": "text", "example": "e"}]
    respx.get(f"{API}/v1/integrations/source-query-keys").respond(200, json={"keys": keys})
    _connections({"id": "c1", "provider": "x:nango"})
    route = respx.post(SOURCES).respond(201, json=_attachment())
    result = runner.invoke(
        app,
        [
            "sources",
            "attach",
            "I1",
            "--provider",
            "x:nango",
            "--type",
            "project",
            flag,
            "a = b",
            "-P",
            PROJECT,
        ],
    )
    assert result.exit_code == 0, result.output
    assert _body(route)["source_query"] == {key: "a = b"}


@respx.mock
def test_attach_sends_a_structured_filter_the_engine_lists():
    _keys()
    _connections({"id": "c3", "provider": "slack:nango"})
    route = respx.post(SOURCES).respond(201, json=_attachment(provider="slack:nango"))
    query = {"slack_filter": {"after": "2025-01-01"}}
    result = runner.invoke(
        app,
        [
            "sources",
            "attach",
            "https://acme.slack.com/archives/C01ABCDEF",
            "--query",
            json.dumps(query),
            "-P",
            PROJECT,
        ],
    )
    assert result.exit_code == 0, result.output
    assert _body(route)["source_query"] == query


@pytest.mark.parametrize(
    ("argv", "says"),
    [
        # A key the engine does not list.
        (["--query", '{"teams_q": "x"}'], "'teams_q' is not a source query key for notion:nango"),
        # A key another provider takes: the ones this provider takes are named.
        (["--jql", "status = Done"], "Use one of: notion_filter, notion_page, notion_view."),
        # A Notion database key on a page: the page's own key is named.
        (
            ["--query", '{"notion_view": "abc"}'],
            "applies to only a database; this item is a page. For a page use one of: notion_page.",
        ),
        # Two queries at once.
        (["--jql", "a", "--query", '{"jql": "b"}'], "Give one query"),
        # Not exactly one key.
        (["--query", '{"jql": "a", "cql": "b"}'], "one key"),
    ],
)
@respx.mock
def test_a_query_the_provider_does_not_take_is_refused_before_sending(argv, says):
    _keys()
    _connections({"id": "c1", "provider": "notion:nango"})
    route = respx.post(SOURCES)
    result = runner.invoke(
        app,
        ["sources", "attach", f"https://www.notion.so/{NOTION_ID}", *argv, "-P", PROJECT],
        terminal_width=400,
    )
    assert result.exit_code == 2, result.output
    # Click's error box wraps the message: read it as one line.
    assert says in " ".join(result.output.replace("│", " ").split())
    assert not route.called


@respx.mock
def test_without_the_engines_key_listing_the_query_is_sent_as_given():
    _keys(404)
    _connections({"id": "c1", "provider": "notion:nango"})
    route = respx.post(SOURCES).respond(201, json=_attachment())
    result = runner.invoke(
        app,
        [
            "sources",
            "attach",
            f"https://www.notion.so/{NOTION_ID}",
            "--query",
            '{"future_key": 1}',
            "-P",
            PROJECT,
        ],
    )
    assert result.exit_code == 0, result.output
    assert _body(route)["source_query"] == {"future_key": 1}


@respx.mock
def test_edit_checks_the_query_against_the_attached_sources_provider():
    _keys()
    respx.get(SOURCES).respond(
        200, json={"data": [_attachment(provider="gmail:nango", external_item_type="all")]}
    )
    route = respx.patch(f"{SOURCES}/s1")
    result = runner.invoke(
        app, ["sources", "edit", "s1", "--drive-q", "x", "-P", PROJECT], terminal_width=400
    )
    assert result.exit_code == 2
    assert "Use one of: gmail_q." in result.output
    assert not route.called


# -- preview ---------------------------------------------------------------------------

PREVIEW = {
    "count": 200,
    "count_capped": True,
    "limit": 200,
    "provider_status": "ok",
    "pull_mode": "agent",
    "source_query": {"jql": "status = Done"},
    "note": None,
    "message": None,
    "breakdown": [],
    "sample": [
        {"id": f"LAB-{n}", "name": f"Issue {n}", "url": f"https://acme.atlassian.net/LAB-{n}"}
        for n in range(12)
    ],
}


@respx.mock
def test_attach_preview_counts_and_attaches_nothing(monkeypatch):
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)
    _keys()
    _connections({"id": "c7", "provider": "jira:nango"})
    attach = respx.post(SOURCES)
    route = respx.post(f"{SOURCES}/rules/preview").respond(200, json=PREVIEW)
    result = runner.invoke(
        app,
        [
            "sources",
            "attach",
            "LAB",
            "--provider",
            "jira",
            "--type",
            "project",
            "--jql",
            "status = Done",
            "--mode",
            "all",
            "--dry-run",
            "-P",
            PROJECT,
        ],
    )
    assert result.exit_code == 0, result.output
    assert not attach.called
    assert _body(route) == {
        "provider": "jira:nango",
        "connector_id": "c7",
        "external_item_id": "LAB",
        "external_item_type": "project",
        "source_query": {"jql": "status = Done"},
        "pull_mode": "all",
    }
    assert "At least 200 items match." in result.stdout
    assert "Issue 9" in result.stdout and "Issue 10" not in result.stdout


@respx.mock
def test_preview_of_an_attached_source_counts_the_saved_query_by_default():
    route = respx.post(f"{SOURCES}/s1/rules/preview").respond(
        200, json={**PREVIEW, "count": 3, "count_capped": False, "sample": []}
    )
    result = runner.invoke(app, ["sources", "preview", "s1", "-P", PROJECT])
    assert result.exit_code == 0, result.output
    assert _body(route) == {}
    assert result.stdout.splitlines()[0] == "ok\tcount=3"

    result = runner.invoke(app, ["sources", "preview", "s1", "--no-query", "-P", PROJECT])
    assert _body(route) == {"source_query": None}


@respx.mock
def test_preview_of_a_candidate_query_and_a_notion_breakdown(monkeypatch):
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)
    _keys()
    respx.get(SOURCES).respond(200, json={"data": [_attachment(external_item_type="database")]})
    route = respx.post(f"{SOURCES}/s1/rules/preview").respond(
        200,
        json={
            **PREVIEW,
            "count": 1,
            "count_capped": False,
            "sample": [{"id": "r1", "name": "Run 1"}],
            "breakdown": [{"id": "d1", "name": "Runs", "count": 1, "count_capped": False}],
        },
    )
    result = runner.invoke(
        app,
        ["sources", "preview", "s1", "--query", '{"notion_view": "abc"}', "-P", PROJECT],
    )
    assert result.exit_code == 0, result.output
    assert _body(route) == {"source_query": {"notion_view": "abc"}}
    assert "1 item matches." in result.stdout
    assert "By database:\n  Runs: 1" in result.stdout
    assert "  Run 1" in result.stdout


@respx.mock
def test_a_query_the_provider_rejects_prints_its_reason_and_exits_1(monkeypatch):
    rejected = {
        **PREVIEW,
        "count": 0,
        "count_capped": False,
        "sample": [],
        "provider_status": "rejected",
        "message": "Field 'stauts' does not exist.",
    }
    respx.post(f"{SOURCES}/s1/rules/preview").respond(200, json=rejected)
    result = runner.invoke(app, ["sources", "preview", "s1", "-P", PROJECT, "--json"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["provider_status"] == "rejected"
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)
    result = runner.invoke(app, ["sources", "preview", "s1", "-P", PROJECT])
    assert result.exit_code == 1
    assert "The provider rejected the query: Field 'stauts' does not exist." in result.stdout


def test_attach_preview_cannot_sync():
    result = runner.invoke(
        app,
        ["sources", "attach", f"https://www.notion.so/{NOTION_ID}", "--preview", "--sync"],
    )
    assert result.exit_code == 2
    assert "cannot --sync" in result.output


# -- keys ------------------------------------------------------------------------------


@respx.mock
def test_keys_lists_what_query_takes_for_a_provider(monkeypatch):
    _keys()
    result = runner.invoke(app, ["sources", "keys", "--provider", "notion"])
    assert result.exit_code == 0, result.output
    assert [line.split("\t")[0] for line in result.stdout.splitlines()] == [
        "notion_filter",
        "notion_view",
        "notion_page",
    ]
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)
    result = runner.invoke(app, ["sources", "keys", "--provider", "jira"])
    assert "jql  [text]  jira:nango" in result.stdout
    assert """--query '{"jql": "status = Done AND labels = customer"}'""" in result.stdout
    assert "No ORDER BY." in result.stdout


@respx.mock
def test_keys_from_an_engine_without_the_listing():
    _keys(404)
    result = runner.invoke(app, ["sources", "keys"])
    assert result.exit_code == 1
    assert "does not list source query keys" in result.output


@respx.mock
def test_a_notion_page_takes_per_database_rules_and_preview_says_what_was_left_out(monkeypatch):
    _keys()
    _connections({"id": "c1", "provider": "notion:nango"})
    attach = respx.post(SOURCES).respond(201, json=_attachment())
    page_rules = {
        "notion_page": {
            "default": {"notion_filter": {"property": "Status", "select": {"equals": "Final"}}},
            "databases": {"d1": {"notion_view": VIEW_ID}, "d2": {"exclude": True}},
        }
    }
    link = f"https://www.notion.so/acme/Lab-Handbook-{NOTION_ID}"
    result = runner.invoke(
        app, ["sources", "attach", link, "--query", json.dumps(page_rules), "-P", PROJECT]
    )
    assert result.exit_code == 0, result.output
    assert _body(attach)["source_query"] == page_rules
    assert _body(attach)["external_item_type"] == "page"

    preview = respx.post(f"{SOURCES}/rules/preview").respond(
        200,
        json={
            **PREVIEW,
            "count": 4,
            "count_capped": False,
            "sample": [],
            "breakdown": [
                {"id": "d1", "name": "Runs", "count": 4, "count_capped": False},
                {
                    "id": "d2",
                    "name": "Archive",
                    "count": 0,
                    "excluded_reason": "excluded by a rule",
                },
            ],
        },
    )
    result = runner.invoke(
        app,
        ["sources", "attach", link, "--query", json.dumps(page_rules), "--preview", "-P", PROJECT],
    )
    assert result.exit_code == 0, result.output
    assert _body(preview)["source_query"] == page_rules
    assert "group\td2\tArchive\tcount=0\texcluded=excluded by a rule" in result.stdout
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)
    result = runner.invoke(
        app,
        ["sources", "attach", link, "--query", json.dumps(page_rules), "--preview", "-P", PROJECT],
    )
    assert "  Runs: 4\n  Archive: left out (excluded by a rule)" in result.stdout
