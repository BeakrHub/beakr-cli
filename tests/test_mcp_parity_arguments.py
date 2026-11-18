"""beakr mcp lists the hosted server's arguments (MCP parity review, 2026-11-14).

The engine's tests/unit/test_remote_mcp_tools.py compares both servers' tools and
arguments, with two documented stdio-only exceptions (``session_scope``, and
``upload_files``'s ``resume_upload_id``). Here, the stdio side of what that brought:

- ``research``, ``wiki_stats`` and ``wiki_graph`` no longer list ``project_id`` (the
  hosted server never took it); it is still read as ``project``, unlisted;
- ``upload_files`` takes the hosted server's ``files`` ({name, text} or {name,
  content_base64}) and ``folder``, checked in its words; ``paths`` is optional;
- ``session_status``'s ``trail`` and ``proposal_id`` stay (the hosted one takes them now).
"""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path

import pytest
import respx

from beakr_cli import mcp_server, mcp_tools_projects
from tests.upload_routes import mock_resumable_uploads

API = "https://api.test"
A = "4dd25154-0d80-44ab-b4c2-167319bfacb4"


@pytest.fixture(autouse=True)
def api(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_person_key_for_tests")


def _listed() -> dict[str, dict]:
    tools = mcp_server.mcp._tool_manager.list_tools()
    return {tool.name: tool.parameters for tool in tools}


def test_project_id_is_not_listed_where_the_hosted_server_takes_project():
    listed = _listed()
    for name in ("research", "wiki_stats", "wiki_graph"):
        assert "project" in listed[name]["properties"]
        assert "project_id" not in listed[name]["properties"], name


@respx.mock
def test_project_id_is_still_read_as_project():
    stats = respx.get(url__regex=rf"{API}/v1/knowledge/stats.*").respond(
        json={"pages": 3, "citations": 1}
    )

    result = asyncio.run(mcp_server.mcp.call_tool("wiki_stats", {"project_id": A}))

    assert not result.is_error, result.content[0].text
    assert stats.calls.last.request.url.params["project_id"] == A


def test_upload_files_lists_files_and_folder_and_paths_is_optional():
    schema = _listed()["upload_files"]
    assert {"files", "folder", "paths", "resume_upload_id"} <= set(schema["properties"])
    assert schema.get("required") == ["project"]


def _project_routes() -> None:
    respx.get(f"{API}/v1/projects/{A}").respond(json={"id": A, "name": "Helios"})
    respx.get(f"{API}/v1/projects").respond(
        json={"data": [{"id": A, "name": "Helios"}], "has_more": False}
    )


@respx.mock
async def test_files_upload_under_their_folder_and_leave_nothing_behind(monkeypatch) -> None:
    _project_routes()
    routes = mock_resumable_uploads(API)
    made: list[Path] = []
    real = mcp_tools_projects.tempfile.mkdtemp

    def mkdtemp(**kwargs):
        made.append(Path(real(**kwargs)))
        return str(made[-1])

    monkeypatch.setattr(mcp_tools_projects.tempfile, "mkdtemp", mkdtemp)
    files = [
        {"name": "notes.md", "text": "Assay notes."},
        {"name": "plot.png", "content_base64": base64.b64encode(b"\x89PNG data").decode()},
        {"name": "empty.txt", "text": ""},
    ]

    text = await mcp_tools_projects.upload_files(A, files=files, folder="reports/2026")
    for upload in list(mcp_tools_projects._UPLOADS.values()):
        if upload.task is not None:
            await asyncio.wait_for(upload.task, timeout=10)

    assert "Uploaded 2 of 2 files" in text and "to Helios" in text
    assert [f["name"] for f in routes["folders"]] == ["reports", "2026"]
    sent = [body.get("title") or body.get("filename") for body in routes["created"]]
    assert len(sent) == 2
    assert "empty.txt" in json.dumps(text) or "empty" in text  # skipped, and said
    assert made and not any(path.exists() for path in made)


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        ({"text": "x"}, "files[0] has no name."),
        ({"name": "a.md"}, "a.md needs its content once"),
        ({"name": "a.md", "text": "x", "content_base64": "eA=="}, "a.md needs its content once"),
        (
            {"name": "a.bin", "content_base64": "not base64!"},
            "content_base64 of a.bin is not base64.",
        ),
    ],
)
@respx.mock
async def test_a_bad_file_is_refused_in_the_hosted_servers_words(entry, message) -> None:
    _project_routes()
    routes = mock_resumable_uploads(API)

    result = await mcp_tools_projects.upload_files(A, files=[entry])

    assert result["error_code"] == "invalid_arguments"
    assert result["message"].startswith(message)
    assert not routes["create"].called


@respx.mock
async def test_nothing_to_upload_names_both_ways() -> None:
    _project_routes()

    result = await mcp_tools_projects.upload_files(A)

    assert result["error_code"] == "invalid_arguments"
    assert "paths" in result["fix"] and "files" in result["fix"]


def test_session_status_keeps_trail_and_proposal_id():
    assert {"trail", "proposal_id"} <= set(_listed()["session_status"]["properties"])
