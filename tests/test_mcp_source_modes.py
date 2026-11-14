"""The read tools' ``sources`` argument: a brief Sources list by default, the full one on request.

ROOT CAUSE (agent tool review, 2026-11-06): every knowledge_base and research
result closed with the full Sources list -- up to 8,000 characters, a
300-character excerpt per document -- with no way to turn it off. It listed
every source the call registered, so a cat repeated each Knowledge page it
linked and a grep or ls repeated every page it had just printed. Now
``sources='brief'`` (the default) gives one line per document, its path, cited
places and source_ref (what a write cites), and leaves out Knowledge pages the
text already names; ``full`` is the old list, byte for byte the hosted
server's; ``none`` drops it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import respx
from httpx import Response
from mcp import types

from beakr_cli import mcp_server
from beakr_cli.mcp_render import SOURCE_MODE_KEY, render, sources_block
from beakr_cli.mcp_server import mcp

API = "https://api.example.com"
CASES = json.loads((Path(__file__).parent / "fixtures" / "knowledge_door_results.json").read_text())

MEMO = {
    "citation_key": "artifact:v1-u1",
    "name": "memo.pdf",
    "filename": "memo.pdf",
    "provider": "google_drive",
    "source_ref": "beakr-source:v1:memo",
    "location": {
        "file_id": "f-1",
        "display_path": "deals/acme/memo.pdf",
        "unit_locator": "p. 7",
        "url": "https://drive.example/memo",
    },
    "evidence": {"excerpt": "Rent is five dollars a square foot."},
}
LINKED_PAGE = {
    "citation_key": "wiki:aaaa",
    "name": "Acme Corp",
    "source_kind": "wiki",
    "source_ref": "beakr-source:v1:wiki:aaaa",
}
SECTION_PAGE = {
    "citation_key": "wiki:bbbb-s1",
    "name": "Northwind Biosciences › Overview",
    "source_kind": "wiki",
    "source_ref": "beakr-source:v1:wiki:bbbb#sec_overview",
}
UNNAMED_PAGE = {
    "citation_key": "wiki:cccc",
    "name": "Kestrel CRO",
    "source_kind": "wiki",
    "source_ref": "beakr-source:v1:wiki:cccc",
}
BODY_TEXT = "[[Acme Corp]] leases from [[Northwind Biosciences]] › Overview (memo.pdf › p. 7)."
SOURCES = [MEMO, LINKED_PAGE, SECTION_PAGE, UNNAMED_PAGE]


@pytest.fixture(autouse=True)
def api(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "test-token")


async def _call(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
    handler = mcp._mcp_server.request_handlers[types.CallToolRequest]
    request = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(name=name, arguments=arguments),
    )
    return (await handler(request)).root


def _body(mode: str | None) -> dict[str, Any]:
    body: dict[str, Any] = {"content": BODY_TEXT, "sources": SOURCES}
    if mode is not None:
        body[SOURCE_MODE_KEY] = mode
    return body


def test_brief_is_one_line_per_document_with_its_source_ref_and_no_excerpt() -> None:
    text = render("knowledge_base", _body("brief"))
    listing = text.split("\n\n", 1)[1]
    lines = listing.splitlines()
    assert lines[0] == (
        "Sources (1 document, 1 Knowledge page; 2 Knowledge pages named above not repeated):"
    )
    assert lines[1] == "- deals/acme/memo.pdf › p. 7 · source_ref beakr-source:v1:memo"
    # A page the text does not name is still listed, so its source_ref is not lost.
    assert lines[2] == "- Kestrel CRO · source_ref beakr-source:v1:wiki:cccc"
    assert lines[3].startswith("(sources='full'")
    assert len(lines) == 4
    assert "Rent is five dollars" not in listing
    assert "https://drive.example/memo" not in listing
    assert "beakr-source:v1:wiki:aaaa" not in listing


def test_brief_falls_back_to_the_read_file_id_when_a_document_has_no_source_ref() -> None:
    record = {key: value for key, value in MEMO.items() if key != "source_ref"}
    listing = sources_block([record], mode="brief")
    assert "- deals/acme/memo.pdf › p. 7 · read_file f-1" in listing


def test_brief_with_every_source_a_page_the_text_names_lists_nothing() -> None:
    body = {"content": BODY_TEXT, "sources": [LINKED_PAGE], SOURCE_MODE_KEY: "brief"}
    assert render("knowledge_base", body) == BODY_TEXT


def test_none_has_no_sources_list() -> None:
    assert render("knowledge_base", _body("none")) == BODY_TEXT


def test_full_and_a_result_without_a_mode_are_the_old_list() -> None:
    full = render("knowledge_base", _body("full"))
    assert full == render("knowledge_base", _body(None))
    assert '"Rent is five dollars a square foot."' in full
    assert "read_file f-1" in full and "beakr-source:v1:wiki:aaaa" in full


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["cat", "grep", "ls", "sections"])
async def test_knowledge_base_defaults_to_brief(command: str) -> None:
    case = CASES["reads"][command]
    call = {"command": command, "arguments": case["arguments"], "scope": case["scope"]}

    async def text(**extra: Any) -> str:
        with respx.mock:
            respx.post(f"{API}/v1/knowledge/wiki/command").mock(
                return_value=Response(200, json=case["rest_response"])
            )
            result = await _call("knowledge_base", {**call, **extra})
        assert not result.isError, result.content
        return result.content[0].text

    brief, full, none = await text(), await text(sources="full"), await text(sources="none")
    # The fixture holds the hosted server's default result, which is the brief
    # list too (``source_listing: brief``); the other modes are the same result
    # with the mode the call asked for.
    hosted = case["mcp"]
    assert hosted[SOURCE_MODE_KEY] == "brief"
    assert brief == render("knowledge_base", hosted)
    assert full == render("knowledge_base", {**hosted, SOURCE_MODE_KEY: "full"})
    assert len(none) <= len(brief) <= len(full)
    assert none == render("knowledge_base", {**hosted, SOURCE_MODE_KEY: "none"})
    # Each document's source_ref the full list names survives: what a write cites.
    for record in case["mcp"]["sources"]:
        ref = str(record.get("source_ref", ""))
        if not ref.startswith("beakr-source:v1:wiki:") and ref in full:
            assert ref in brief, record
    if command in ("ls", "grep"):
        # Every page a listing registered is a page it printed.
        assert "beakr-source:v1:wiki:" not in brief


@pytest.mark.asyncio
async def test_an_unknown_mode_is_refused() -> None:
    result = await _call("knowledge_base", {"command": "ls", "sources": "all"})
    assert result.isError
    assert "sources" in result.content[0].text


def _research_reply() -> dict[str, Any]:
    return {
        "text": "Rent is $5 (memo.pdf › p. 7), per [[Acme Corp]].",
        "citations": [MEMO, LINKED_PAGE],
        "answer": "",
        "sources": {},
    }


@pytest.mark.asyncio
@respx.mock
async def test_research_lists_its_sources_briefly_by_default() -> None:
    respx.post(f"{API}/v1/knowledge/research").respond(200, json=_research_reply())
    brief = (await mcp_server.research(query="Rent?"))["content"]
    assert "- deals/acme/memo.pdf › p. 7 · source_ref beakr-source:v1:memo" in brief
    assert "Rent is five dollars" not in brief
    assert "1 Knowledge page named above not repeated" in brief
    full = (await mcp_server.research(query="Rent?", sources="full"))["content"]
    assert '"Rent is five dollars a square foot."' in full
    none = (await mcp_server.research(query="Rent?", sources="none"))["content"]
    assert "Sources" not in none and "source_ref" not in none
    assert none.startswith("Rent is $5 (memo.pdf › p. 7)")
