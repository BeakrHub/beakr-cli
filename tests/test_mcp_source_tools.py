"""The stdio ``project_sources``, ``attach_source``, ``update_source``,
``preview_source_rules``, ``sync_source`` and ``remove_source`` tools.

The hosted server has the same six tools (beakr-engine
``beakr/api/mcp_remote/source_tools.py``), and its
``tests/unit/test_mcp_every_tool_result_contract.py`` holds both servers to the
same answer. These pin what this server sends for each call, what it refuses
before sending anything, and the hosted text, line for line, for the answers that
contract test uses.

The rules that matter most: attaching leaves Knowledge off unless the caller
asks (turning it on starts reading the source and spending ingest budget), and
an empty filter or guidance never clears a saved one.
"""

from __future__ import annotations

import json

import pytest
import respx

from beakr_cli import mcp_server, mcp_tools_platform, mcp_tools_projects, tool_surface
from tests import mcp_client

API = "https://api.test"
PROJECT = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
SOURCE = "4dd25154-0d80-44ab-b4c2-167319bfacb4"
JOB = "8b2f6c10-3d4e-4f5a-9b6c-7d8e9f0a1b2c"
OTHER = "0b1e8a64-0c41-4d5e-9b0a-1f2e3d4c5b6a"
SOURCES = f"{API}/v1/projects/{PROJECT}/sources"
ONE = f"{SOURCES}/{SOURCE}"

FILTER = {"drive_q": "mimeType = 'application/pdf'"}
ATTACHED = {
    "id": SOURCE,
    "provider": "gdrive:nango",
    "external_item_id": "1AbC",
    "external_item_type": "folder",
    "external_item_name": "Trial protocols",
    "ingest_wiki": False,
    "auto_sync_wiki": False,
    "supports_wiki_ingest": True,
    "sync_status": None,
    "last_sync_at": None,
    "source_query": FILTER,
    "guidance": "Protocols and amendments only.",
}
LINE = (
    f"- Trial protocols (gdrive:nango folder) attachment_id {SOURCE}: Knowledge off, "
    "nightly sync off, never synced; filter {'drive_q': \"mimeType = 'application/pdf'\"}; "
    "guidance 'Protocols and amendments only.'."
)
LINE_ON = LINE.replace("Knowledge off", "Knowledge on")
ITEM = {
    "project": PROJECT,
    "provider": "gdrive:nango",
    "item_id": "1AbC",
    "item_type": "folder",
    "name": "Trial protocols",
}
NAME_SOURCE = {
    "status": "error",
    "error_code": "invalid_arguments",
    "message": "Name the source by its attachment_id, a UUID.",
    "fix": "Pass attachment_id exactly as project_sources prints it.",
}
SIX = (
    "project_sources",
    "attach_source",
    "update_source",
    "preview_source_rules",
    "sync_source",
    "remove_source",
)


@pytest.fixture(autouse=True)
def api(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_person_key_for_tests")
    mcp_tools_platform._resolved_projects.clear()
    yield
    mcp_tools_platform._resolved_projects.clear()


def _project() -> None:
    respx.get(f"{API}/v1/projects/{PROJECT}").respond(json={"id": PROJECT, "name": "Assays"})


def _sent(route) -> dict:
    return json.loads(route.calls.last.request.content)


# -- listing -----------------------------------------------------------------------------


async def test_the_tools_take_the_hosted_tools_arguments() -> None:
    listed = {tool.name: tool for tool in await mcp_server.mcp.list_tools()}
    expected = {
        "project_sources": ({"project", "attachment_id"}, ["project"]),
        "attach_source": (
            {
                "project",
                "provider",
                "item_id",
                "item_type",
                "name",
                "url",
                "connector_id",
                "knowledge",
                "nightly_sync",
                "filter",
                "guidance",
            },
            ["project", "provider", "item_id", "item_type", "name"],
        ),
        "update_source": (
            {
                "project",
                "attachment_id",
                "knowledge",
                "nightly_sync",
                "filter",
                "guidance",
                "clear_filter",
                "clear_guidance",
            },
            ["project", "attachment_id"],
        ),
        "preview_source_rules": (
            {"project", "attachment_id", "filter"},
            ["project", "attachment_id"],
        ),
        "sync_source": ({"project", "attachment_id"}, ["project", "attachment_id"]),
        "remove_source": ({"project", "attachment_id"}, ["project", "attachment_id"]),
        "tracked_items": (
            {
                "project",
                "attachment_id",
                "state",
                "reason_code",
                "search",
                "since",
                "cursor",
                "limit",
            },
            ["project", "attachment_id"],
        ),
    }
    for name, (properties, required) in expected.items():
        schema = listed[name].input_schema
        # The self-hosted line adds confirm_lock where the shared surface lists the
        # tool (tests/test_lock_confirm.py); the hosted tool's own arguments are the rest.
        assert set(schema["properties"]) - {"confirm_lock"} == properties, name
        assert schema["required"] == required, name
        assert listed[name].description == tool_surface.description(name), name


async def test_the_hints_say_which_tools_read_which_change_and_which_remove() -> None:
    listed = {tool.name: tool.annotations for tool in await mcp_server.mcp.list_tools()}

    def hints(name: str) -> tuple:
        found = listed[name]
        return (
            found.read_only_hint,
            found.destructive_hint,
            found.idempotent_hint,
            found.open_world_hint,
        )

    assert hints("project_sources")[:2] == (True, False)
    # The preview asks the connected service what matches; nothing in Beakr changes.
    assert hints("preview_source_rules") == (True, False, True, True)
    assert hints("attach_source") == (False, False, True, True)
    assert hints("update_source") == (False, False, True, False)
    # Each call may start a sync, which reads the service and spends ingest budget.
    assert hints("sync_source") == (False, False, False, True)
    assert hints("remove_source") == (False, True, True, False)
    assert hints("tracked_items")[:2] == (True, False)


# -- project_sources ---------------------------------------------------------------------


@respx.mock
async def test_project_sources_lists_each_source_on_one_line():
    _project()
    route = respx.get(SOURCES).respond(json={"data": [ATTACHED], "next_cursor": None})
    text = await mcp_tools_projects.project_sources(PROJECT)
    assert dict(route.calls.last.request.url.params) == {"limit": "100"}
    assert text == f"1 connected source(s):\n{LINE}"


@respx.mock
async def test_project_sources_with_none_attached_names_the_tool_that_adds_one():
    _project()
    respx.get(SOURCES).respond(json={"data": [], "next_cursor": None})
    assert await mcp_tools_projects.project_sources(PROJECT) == (
        "This project has no connected sources. attach_source adds one from a "
        "connected integration."
    )


@respx.mock
async def test_project_sources_follows_the_cursor_to_the_last_page():
    _project()
    second = {**ATTACHED, "id": OTHER, "external_item_name": "Amendments"}
    route = respx.get(SOURCES).mock(
        side_effect=[
            respx.MockResponse(200, json={"data": [ATTACHED], "next_cursor": "c1"}),
            respx.MockResponse(200, json={"data": [second], "next_cursor": None}),
        ]
    )
    text = await mcp_tools_projects.project_sources(PROJECT)
    assert route.call_count == 2
    assert dict(route.calls.last.request.url.params) == {"limit": "100", "cursor": "c1"}
    assert text.startswith("2 connected source(s):\n")
    assert "More sources exist" not in text


@respx.mock
async def test_project_sources_stops_at_ten_pages_and_says_more_exist():
    _project()
    route = respx.get(SOURCES).respond(json={"data": [ATTACHED], "next_cursor": "again"})
    text = await mcp_tools_projects.project_sources(PROJECT)
    assert route.call_count == 10
    assert text.splitlines()[0] == "10 connected source(s):"
    assert text.splitlines()[-1] == "More sources exist than are listed here."


@respx.mock
async def test_project_sources_for_one_source_adds_how_far_its_sync_has_got():
    _project()
    respx.get(SOURCES).respond(
        json={
            "data": [{**ATTACHED, "ingest_wiki": True, "sync_status": "syncing"}],
            "next_cursor": None,
        }
    )
    status = respx.get(f"{ONE}/sync-status").respond(
        json={"status": "syncing", "files_completed": 4, "files_expected": 10, "files_failed": 1}
    )
    text = await mcp_tools_projects.project_sources(PROJECT, attachment_id=SOURCE)
    assert status.call_count == 1
    assert text == (
        LINE_ON.replace("never synced", "syncing now") + "\nSync: syncing, 4 of 10 items, 1 failed."
    )


@respx.mock
async def test_project_sources_for_a_source_the_project_does_not_have_is_refused():
    _project()
    respx.get(SOURCES).respond(json={"data": [ATTACHED], "next_cursor": None})
    status = respx.get(f"{SOURCES}/{OTHER}/sync-status").respond(json={})
    result = await mcp_tools_projects.project_sources(PROJECT, attachment_id=OTHER)
    assert result == {
        "status": "error",
        "error_code": "not_found",
        "message": "No source with that attachment_id is attached to this project.",
        "fix": "Call project_sources without attachment_id to list them.",
    }
    assert not status.called


# -- attach_source -----------------------------------------------------------------------


@respx.mock
async def test_attach_leaves_knowledge_off_unless_asked_and_says_nothing_was_read():
    _project()
    route = respx.post(SOURCES).respond(201, json=ATTACHED)
    text = await mcp_tools_projects.attach_source(**ITEM)
    # Off is sent, not left to the route's default: attaching must read nothing.
    assert _sent(route) == {
        "provider": "gdrive:nango",
        "external_item_id": "1AbC",
        "external_item_type": "folder",
        "external_item_name": "Trial protocols",
        "ingest_wiki": False,
        "auto_sync_wiki": False,
    }
    assert text == (
        f"Attached.\n{LINE}\n"
        "Knowledge is off for it, so nothing has been read. update_source with "
        "knowledge=true turns it on and starts the first sync, after the person agrees: "
        "that reads the source and spends the organization's ingest budget. Set a filter "
        "or guidance first if the source is large (preview_source_rules shows what a "
        "filter keeps)."
    )


@respx.mock
async def test_attach_with_knowledge_sends_it_on_with_the_link_filter_and_guidance():
    _project()
    route = respx.post(SOURCES).respond(
        201, json={**ATTACHED, "ingest_wiki": True, "auto_sync_wiki": True}
    )
    text = await mcp_tools_projects.attach_source(
        **ITEM,
        url=" https://drive.example/1AbC ",
        connector_id="conn-1",
        knowledge=True,
        nightly_sync=True,
        filter=FILTER,
        guidance="  Protocols and amendments only. ",
    )
    assert _sent(route) == {
        "provider": "gdrive:nango",
        "external_item_id": "1AbC",
        "external_item_type": "folder",
        "external_item_name": "Trial protocols",
        "external_item_url": "https://drive.example/1AbC",
        "connector_id": "conn-1",
        "ingest_wiki": True,
        "auto_sync_wiki": True,
        "source_query": FILTER,
        "guidance": "Protocols and amendments only.",
    }
    assert text.splitlines()[0] == "Attached."
    assert text.splitlines()[-1] == "Knowledge is on. sync_source starts the first sync."


@respx.mock
async def test_nightly_sync_without_knowledge_is_not_turned_on():
    _project()
    route = respx.post(SOURCES).respond(201, json=ATTACHED)
    await mcp_tools_projects.attach_source(**ITEM, nightly_sync=True, filter={}, guidance=" ")
    sent = _sent(route)
    assert (sent["ingest_wiki"], sent["auto_sync_wiki"]) == (False, False)
    # An empty filter and blank guidance are a model filling optional properties.
    assert "source_query" not in sent and "guidance" not in sent


@respx.mock
async def test_attach_without_what_names_the_item_is_refused_before_anything_is_sent():
    result = await mcp_tools_projects.attach_source(**{**ITEM, "item_id": "  "})
    assert result["error_code"] == "invalid_arguments"
    assert result["message"] == (
        "Attaching needs the provider, the item's id, its type and its name."
    )
    assert result["fix"].startswith("Pass provider (as integrations command='list' prints it")
    assert not respx.calls


# -- update_source -----------------------------------------------------------------------


@respx.mock
async def test_turning_knowledge_on_sends_only_that_and_points_at_the_first_sync():
    _project()
    route = respx.patch(ONE).respond(json={**ATTACHED, "ingest_wiki": True})
    text = await mcp_tools_projects.update_source(PROJECT, SOURCE, knowledge=True)
    assert _sent(route) == {"ingest_wiki": True}
    assert text == (
        f"Saved.\n{LINE_ON}\nKnowledge was turned on. sync_source starts the first sync now."
    )


@respx.mock
async def test_a_new_filter_and_guidance_are_saved_and_the_answer_stops_at_the_source():
    _project()
    route = respx.patch(ONE).respond(json=ATTACHED)
    text = await mcp_tools_projects.update_source(
        PROJECT, SOURCE, nightly_sync=False, filter=FILTER, guidance=" Protocols only. "
    )
    assert _sent(route) == {
        "auto_sync_wiki": False,
        "source_query": FILTER,
        "guidance": "Protocols only.",
    }
    assert text == f"Saved.\n{LINE}"


@respx.mock
async def test_an_empty_filter_or_guidance_changes_nothing_and_nothing_is_sent():
    result = await mcp_tools_projects.update_source(PROJECT, SOURCE, filter={}, guidance="  ")
    assert result == {
        "status": "error",
        "error_code": "invalid_arguments",
        "message": "Nothing to change.",
        "fix": "Pass at least one of knowledge, nightly_sync, filter, guidance, clear_filter "
        "or clear_guidance.",
    }
    assert not respx.calls


@respx.mock
async def test_an_empty_filter_beside_a_real_change_leaves_the_saved_filter_alone():
    _project()
    route = respx.patch(ONE).respond(json=ATTACHED)
    await mcp_tools_projects.update_source(
        PROJECT, SOURCE, knowledge=False, filter={}, guidance="", clear_filter=False
    )
    assert _sent(route) == {"ingest_wiki": False}


@respx.mock
async def test_clear_filter_and_clear_guidance_send_null_for_each():
    _project()
    route = respx.patch(ONE).respond(json={**ATTACHED, "source_query": None, "guidance": None})
    text = await mcp_tools_projects.update_source(
        # Clearing wins over a value sent in the same call.
        PROJECT,
        SOURCE,
        filter=FILTER,
        clear_filter=True,
        clear_guidance=True,
    )
    assert _sent(route) == {"source_query": None, "guidance": None}
    assert text.endswith("never synced; no filter or guidance.")


# -- preview_source_rules ----------------------------------------------------------------


@respx.mock
async def test_preview_without_a_filter_sends_an_empty_body_for_the_saved_filter():
    _project()
    route = respx.post(f"{ONE}/rules/preview").respond(
        json={
            "source_query": FILTER,
            "count": 3,
            "count_capped": False,
            "limit": 500,
            "sample": [{"id": "f1", "title": "Protocol v2.pdf"}],
            "breakdown": [],
            "provider_status": "ok",
            "message": None,
        }
    )
    text = await mcp_tools_projects.preview_source_rules(PROJECT, SOURCE, filter={})
    assert _sent(route) == {}
    assert text == (
        "3 item(s) match under filter {'drive_q': \"mimeType = 'application/pdf'\"}.\n"
        "- Protocol v2.pdf"
    )


@respx.mock
async def test_preview_of_a_candidate_filter_sends_it_and_reports_a_capped_count():
    _project()
    candidate = {"drive_q": "name contains 'protocol'"}
    route = respx.post(f"{ONE}/rules/preview").respond(
        json={
            "source_query": candidate,
            "count": 500,
            "count_capped": True,
            "sample": [{"id": "f1", "name": "a.pdf"}, {"id": "f2"}],
            "breakdown": [{"name": "Studies", "count": 400}],
            "provider_status": "ok",
            "message": "Counted the first 500.",
            "dry_run": {"would_ingest": 120},
        }
    )
    text = await mcp_tools_projects.preview_source_rules(PROJECT, SOURCE, filter=candidate)
    assert _sent(route) == {"source_query": candidate}
    assert text == (
        "At least 500 item(s) match under filter {'drive_q': \"name contains 'protocol'\"}.\n"
        "- a.pdf\n"
        "- f2\n"
        "  Studies: 400\n"
        "Counted the first 500.\n"
        "A sync would bring in 120 of them."
    )


@respx.mock
async def test_preview_says_when_the_service_refused_the_filter_or_nothing_matches():
    _project()
    route = respx.post(f"{ONE}/rules/preview")
    route.respond(json={"provider_status": "rejected", "message": "Invalid query"})
    refused = await mcp_tools_projects.preview_source_rules(
        PROJECT, SOURCE, filter={"drive_q": "("}
    )
    assert refused == "The service refused this filter: Invalid query"
    route.respond(json={"source_query": FILTER, "count": 0, "provider_status": "ok"})
    empty = await mcp_tools_projects.preview_source_rules(PROJECT, SOURCE)
    assert empty.splitlines()[-1] == (
        "Nothing matches, so a sync would bring nothing in. Loosen the filter."
    )


# -- sync_source and remove_source -------------------------------------------------------


@respx.mock
async def test_sync_source_posts_no_body_and_answers_the_hosted_text():
    _project()
    route = respx.post(f"{ONE}/sync").respond(
        json={"status": "started", "message": "Sync started", "job_id": JOB}
    )
    text = await mcp_tools_projects.sync_source(PROJECT, SOURCE)
    assert route.call_count == 1
    assert route.calls.last.request.content in (b"", b"null")
    assert text == (
        "Sync started (Sync started). project_sources with this attachment_id reports its "
        "progress; cancel_sync stops it."
    )


@respx.mock
async def test_remove_source_deletes_it_and_says_knowledge_is_kept():
    _project()
    route = respx.delete(ONE).respond(204)
    text = await mcp_tools_projects.remove_source(PROJECT, f" {SOURCE} ")
    assert route.call_count == 1
    assert text == (
        f"Removed the source {SOURCE} from the project. Knowledge already built from it "
        "is kept; it is no longer synced."
    )


_NEEDS_A_SOURCE = [
    mcp_tools_projects.update_source,
    mcp_tools_projects.preview_source_rules,
    mcp_tools_projects.sync_source,
    mcp_tools_projects.remove_source,
    mcp_tools_projects.tracked_items,
]


@pytest.mark.parametrize("tool", _NEEDS_A_SOURCE)
@respx.mock
async def test_a_call_that_names_no_source_is_refused_before_anything_is_sent(tool):
    assert await tool(PROJECT, "  ") == NAME_SOURCE
    assert not respx.calls


@pytest.mark.parametrize("tool", _NEEDS_A_SOURCE)
@pytest.mark.parametrize(
    "given", ["../../members", f"{SOURCE}/sync", f"{SOURCE}?x=1", "s1", SOURCE.replace("-", "")]
)
@respx.mock
async def test_an_attachment_id_that_is_not_a_uuid_never_becomes_a_path(tool, given):
    # The id is a path segment of a request made as the signed-in person: anything
    # but a UUID could name a different route, so it is refused, never sent.
    assert await tool(PROJECT, given) == NAME_SOURCE
    assert not respx.calls


@respx.mock
async def test_project_sources_refuses_a_given_attachment_id_that_is_not_a_uuid():
    _project()
    listing = respx.get(SOURCES).respond(json={"data": [ATTACHED], "next_cursor": None})
    result = await mcp_tools_projects.project_sources(PROJECT, attachment_id="../../members")
    assert result == NAME_SOURCE
    assert not listing.called


@respx.mock
async def test_an_attachment_id_in_capitals_is_sent_in_its_canonical_form():
    _project()
    route = respx.post(f"{ONE}/sync").respond(json={"status": "started"})
    text = await mcp_tools_projects.sync_source(PROJECT, SOURCE.upper())
    assert route.call_count == 1
    assert text.startswith("Sync started (started).")


# -- through the server ------------------------------------------------------------------


@respx.mock
async def test_scope_is_read_as_the_project_on_every_source_tool():
    _project()
    respx.get(SOURCES).respond(json={"data": [ATTACHED], "next_cursor": None})
    result = await mcp_client.call_tool("project_sources", {"scope": PROJECT})
    assert not result.is_error
    assert result.content[0].text.startswith("1 connected source(s):")


@respx.mock
async def test_a_refused_sync_is_an_error_the_agent_can_read():
    _project()
    respx.post(f"{ONE}/sync").respond(
        409,
        json={
            "error": {
                "code": "knowledge_off",
                "message": "Knowledge is off for this source.",
                "fix": "Turn Knowledge on for the source first.",
            }
        },
    )
    result = await mcp_client.call_tool(
        "sync_source", {"project": PROJECT, "attachment_id": SOURCE}
    )
    assert result.is_error
    sent = json.dumps(json.loads(result.content[0].text))
    assert "Knowledge is off for this source." in sent


def test_the_six_tools_are_in_the_surface_this_server_lists() -> None:
    assert set(SIX) <= {tool.__name__ for tool in mcp_tools_projects.PROJECT_TOOLS}


# -- project_sources: what became of the items a sync brought in -------------------------

OUTCOMES = {
    "since": "2026-11-23T10:00:00+00:00",
    "until": None,
    "total": 4,
    "counts": {
        "ingested": 2,
        "skipped": 1,
        "pending": 0,
        "removed": 0,
        "failed": 1,
        "cancelled": 0,
    },
    "reasons": [
        {"outcome": "skipped", "reason_code": "nothing_to_add", "count": 1},
        {"outcome": "failed", "reason_code": "permission_denied", "count": 1},
    ],
    "items": [
        {
            "external_id": "conn:thread-1",
            "title": "Lunch order",
            "outcome": "skipped",
            "reason_code": "nothing_to_add",
            "reason": "A lunch order; nothing for the project.",
        },
        {
            "external_id": None,
            "title": "Locked.pdf",
            "outcome": "failed",
            "reason_code": "permission_denied",
            "reason": "No permission.",
        },
        {
            "external_id": "conn:t2",
            "title": "Protocol",
            "outcome": "ingested",
            "reason_code": None,
            "reason": None,
        },
    ],
    "items_limit": 25,
    "has_more": True,
    "tracked_items_path": "/v1/projects/p/sources/a/tracked-items?since=x",
}


@respx.mock
async def test_project_sources_for_one_source_says_what_knowledge_set_aside_and_why():
    _project()
    respx.get(SOURCES).respond(
        json={"data": [{**ATTACHED, "ingest_wiki": True}], "next_cursor": None}
    )
    respx.get(f"{ONE}/sync-status").respond(
        json={
            "status": "completed",
            "files_completed": 1,
            "files_total": 2,
            "files_failed": 1,
            "outcomes": OUTCOMES,
        }
    )
    text = await mcp_tools_projects.project_sources(PROJECT, attachment_id=SOURCE)
    assert text.splitlines()[1:] == [
        "Sync: completed, 1 of 2 items, 1 failed.",
        "Items: 2 ingested, 1 skipped, 1 failed.",
        "Why: nothing_to_add 1, permission_denied 1.",
        "- Lunch order [skipped]: A lunch order; nothing for the project. (nothing_to_add)",
        "- Locked.pdf [failed]: No permission. (permission_denied)",
    ]


def test_a_sync_with_outcomes_nobody_has_listed_points_at_tracked_items():
    from beakr_cli import connected_sources

    lines = connected_sources.outcomes_text(
        {**OUTCOMES, "counts": {**OUTCOMES["counts"], "pending": 30}}
    )
    assert lines[-1] == (
        "tracked_items with this attachment_id lists every item and why it was set aside."
    )
    assert connected_sources.outcomes_text({"counts": {}}) == [
        "Items: none brought in by this sync yet."
    ]


# -- tracked_items -----------------------------------------------------------------------

TRACKED_PAGE = {
    "data": [
        {
            "external_id": "conn:thread-1",
            "title": "Lunch order",
            "kind": "thread",
            "state": "skipped",
            "reason_code": "nothing_to_add",
            "reason": "A lunch order; nothing for the project.",
        },
        {"external_id": "conn:t2", "title": "Protocol", "kind": "thread", "state": "ingested"},
    ],
    "next_cursor": "c2",
    "has_more": True,
    "provider": "gmail",
    "kind": "thread",
    "counts": {"total": 5, "pending": 1, "ingested": 3, "skipped": 1, "removed": 0},
    "matched": 2,
}


@respx.mock
async def test_tracked_items_sends_the_filters_and_answers_one_page_with_how_to_continue():
    _project()
    route = respx.get(f"{ONE}/tracked-items").respond(json=TRACKED_PAGE)
    result = await mcp_tools_projects.tracked_items(
        PROJECT, SOURCE, state="skipped", reason_code=" nothing_to_add ", search="", cursor="c1"
    )
    assert dict(route.calls.last.request.url.params) == {
        "limit": "50",
        "cursor": "c1",
        "state": "skipped",
        "reason_code": "nothing_to_add",
    }
    assert result["coverage"] == {
        "returned": 2,
        "has_more": True,
        "next_cursor": "c2",
        "complete": False,
    }
    hint = (
        'More results: call tracked_items again with cursor="c2" and the same project, '
        "attachment_id and filters."
    )
    assert result["next"] == [hint]
    assert result["content"] == "\n".join(
        [
            "5 tracked item(s) from gmail: 3 ingested, 1 skipped, 1 pending, 0 removed.",
            "2 match the filters.",
            "- Lunch order [skipped]: A lunch order; nothing for the project. (nothing_to_add)",
            "- Protocol [ingested]",
            "",
            f"[{hint}]",
        ]
    )


@respx.mock
async def test_tracked_items_with_nothing_matching_says_so_and_is_complete():
    _project()
    respx.get(f"{ONE}/tracked-items").respond(
        json={
            "data": [],
            "next_cursor": None,
            "has_more": False,
            "provider": "gdrive",
            "counts": {"total": 2, "ingested": 2},
            "matched": 0,
        }
    )
    result = await mcp_tools_projects.tracked_items(PROJECT, SOURCE, search="nothing")
    assert result["coverage"]["complete"] is True and result["next"] == []
    assert result["content"].splitlines()[-1] == "No tracked items match."


@respx.mock
async def test_tracked_items_over_the_page_maximum_asks_for_fifty_and_says_so():
    _project()
    route = respx.get(f"{ONE}/tracked-items").respond(json={**TRACKED_PAGE, "has_more": False})
    result = await mcp_client.call_tool(
        "tracked_items", {"project": PROJECT, "attachment_id": SOURCE, "limit": 100}
    )
    assert not result.is_error, result.content[0].text
    assert route.calls.last.request.url.params["limit"] == "50"
    assert result.content[0].text.startswith("Repaired this call: reduced `limit` from 100 to 50")
