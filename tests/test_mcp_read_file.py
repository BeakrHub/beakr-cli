"""``read_file`` opens the document a Knowledge citation names, at the cited place.

ROOT CAUSE (agent evaluation traces: all ~79 read_file calls returned
``Not found`` or ``(characters 0-0 of 0)``): agents passed the uuid inside a
citation token, which is the cited VERSION, and the extraction view the tool read
served its text page by page. The tool then grew a version-to-file lookup and a
page walk, and still could not open a file by the path a Sources line printed.

Now one documented route resolves and reads every reference a Knowledge read
hands out (``POST /v1/artifacts/read``): a file id, a version id, a citation
token, or the file's path, each optionally with ``#<unit>`` (``memo.pdf#p. 7``).
These tests pin that every form is exactly one call to that route, that the
header names ``path#unit`` (the form read_file takes back), and that failures
answer as the hosted server does.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
import pytest
import respx

from beakr_cli import mcp_server
from beakr_cli.transcript_activity import SessionActivity

API = "https://api.example.com"
READ = f"{API}/v1/artifacts/read"
_CAT = json.loads((Path(__file__).parent / "fixtures" / "knowledge_door_results.json").read_text())[
    "reads"
]["cat"]["mcp"]
TOKEN = re.findall(r"\{\{artifact:[0-9a-f-]{36}-[^}]+\}\}", _CAT["content"])[0]
VERSION = re.search(r"[0-9a-f-]{36}", TOKEN).group(0)
FILE_ID = "6f1c2d3e-4a5b-4c6d-8e7f-901234567890"
PATH = "deals/acme/closing/charter.pdf"


@pytest.fixture(autouse=True)
def api(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "test-token")


def _window(*, unit: dict | None = None, text: str = "The charter.", **extra) -> dict:
    return {
        "file": {
            "id": FILE_ID,
            "version_id": VERSION,
            "title": "charter.pdf",
            "display_path": PATH,
            "upload_path": None,
        },
        "unit": unit,
        "text": text,
        "start": 0,
        "end": len(text),
        "total_chars": 900,
        "has_more": False,
        "next_offset": None,
        "next_reference": None,
        **extra,
    }


async def _read(arguments: dict) -> str:
    result = await mcp_server.mcp.call_tool("read_file", arguments)
    assert not result.isError, result.content
    return result.content[0].text


async def _read_error(arguments: dict) -> dict:
    result = await mcp_server.mcp.call_tool("read_file", arguments)
    assert result.isError, result.content
    return json.loads(result.content[0].text)


@pytest.mark.parametrize(
    "arguments, sent",
    [
        ({"artifact_id": FILE_ID}, FILE_ID),
        ({"artifact_id": TOKEN}, TOKEN),
        ({"path": PATH}, PATH),
        ({"path": f"{PATH}#p. 7"}, f"{PATH}#p. 7"),
        ({"artifact_id": "Closing/charter.pdf › p. 7"}, "Closing/charter.pdf › p. 7"),
    ],
)
@respx.mock
async def test_every_reference_form_is_one_call_to_the_read_route(arguments, sent):
    route = respx.post(READ).respond(json=_window())
    await _read(arguments)
    assert respx.calls.call_count == 1
    assert json.loads(route.calls.last.request.content) == {
        "reference": sent,
        "offset": 0,
        "max_chars": 20_000,
    }


@respx.mock
async def test_a_unit_read_names_path_and_place_and_the_next_page():
    respx.post(READ).respond(
        json=_window(
            unit={"locator": "p. 7", "found": True, "page": 7, "page_count": 9},
            text="Page seven: NW-217 is the lead.",
            has_more=True,
            next_reference=f"{PATH}#p. 8",
        )
    )
    text = await _read({"path": f"{PATH}#p. 7"})
    assert text.startswith(
        f"{PATH} › p. 7 (page 7 of 9, characters 0-31 of 900; file {FILE_ID} version {VERSION})"
    )
    assert "NW-217 is the lead." in text
    assert f"Next page: call read_file with '{PATH}#p. 8'." in text


@respx.mock
async def test_a_plain_read_pages_on_with_offset():
    route = respx.post(READ).mock(
        side_effect=[
            httpx.Response(200, json=_window(text="a" * 40, has_more=True, next_offset=40)),
            httpx.Response(200, json={**_window(text="b" * 10), "start": 40, "end": 50}),
        ]
    )
    first = await _read({"artifact_id": FILE_ID, "limit": 40})
    assert "More text follows: call read_file again with offset=40." in first
    rest = await _read({"artifact_id": FILE_ID, "offset": 40})
    assert json.loads(route.calls.last.request.content)["offset"] == 40
    assert "characters 40-50 of 900" in rest and "More text follows" not in rest


@respx.mock
async def test_a_unit_that_is_not_found_says_so():
    respx.post(READ).respond(
        json=_window(unit={"locator": "slide 40", "found": False, "page": None, "page_count": None})
    )
    text = await _read({"path": f"{PATH}#slide 40"})
    assert "That place was not found in this version of the file; this is its start." in text


@respx.mock
async def test_an_unreachable_reference_answers_as_the_hosted_server():
    respx.post(READ).respond(
        404,
        json={
            "error": {"code": "not_found", "message": "No readable file matches this reference."}
        },
    )
    assert await _read_error({"artifact_id": VERSION}) == {
        "status": "error",
        "message": "No readable file matches this reference. Pass `artifact_id`: the file id, "
        "citation token or path from a Knowledge result's Sources (location.file_id, "
        "or `read_file <id>`).",
    }


async def test_an_empty_reference_is_refused_before_any_call():
    error = await _read_error({"artifact_id": "  "})
    assert error["error_code"] == "invalid_arguments"


@respx.mock
async def test_an_ambiguous_path_lists_the_files_it_names():
    respx.post(READ).respond(
        409,
        json={
            "error": {
                "code": "ambiguous_reference",
                "message": "This path names more than one file you can read; pass one of these "
                "files' ids (or a longer path that names one).",
                "fix": "Pass the id of one of the files the error lists, or a longer path that "
                "names one file.",
            },
            "detail": {
                "files": [
                    {"id": "f-a", "display_path": "matters/a/Closing/memo.pdf"},
                    {"id": "f-b", "display_path": "matters/b/Closing/memo.pdf"},
                ]
            },
        },
    )
    error = await _read_error({"path": "Closing/memo.pdf"})
    assert error["error_code"] == "ambiguous_reference"
    assert "- matters/a/Closing/memo.pdf (read_file f-a)" in error["message"]
    assert "- matters/b/Closing/memo.pdf (read_file f-b)" in error["message"]
    assert error["fix"].startswith("Pass the id of one of the files")


@respx.mock
async def test_a_file_with_no_readable_text_is_an_error():
    respx.post(READ).respond(
        409,
        json={
            "error": {
                "code": "no_readable_text",
                "message": "This file has no readable text yet: it is still being processed, "
                "or its type has no text to read.",
                "fix": "Retry once the file finishes processing, or cite its Knowledge "
                "excerpts instead.",
            }
        },
    )
    error = await _read_error({"artifact_id": FILE_ID})
    assert error["error_code"] == "no_readable_text"
    assert error["message"].startswith("This file has no readable text yet")


@respx.mock
async def test_the_transcript_records_the_file_and_version_read():
    respx.post(READ).respond(json=_window())
    text = await _read({"artifact_id": TOKEN})
    session = SessionActivity()
    session.call("c1", "mcp__beakr__read_file", {"artifact_id": TOKEN})
    session.result("c1", [{"type": "text", "text": text}])
    assert session.sources_read() == [{"document_id": FILE_ID, "version_id": VERSION}]
