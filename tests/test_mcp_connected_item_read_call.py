"""The stdio server prints the call that re-reads a cited note or thread, as the hosted one does.

ROOT CAUSE (live check 2026-11-13, calls 02_sections, 05_cat and 07_sources):
the REST door attaches to a cited Granola note or Slack thread its provider read
call (``retrieval: {tool, arguments}``), and ``mcp_render`` prints one as
``read with integrations command='call' ...``. The stdio server built each
public source with ``beakr_sdk.sources.public_source``, which up to 0.2.x keeps
a ``retrieval`` only when it carries a file download ``handle``, so the Sources
line ended at the ``source_ref`` while the hosted server named the call. An
agent was left with the share link, whose id the read tool refuses.

The lines asserted here are the hosted server's, byte for byte, from that run.
"""

from __future__ import annotations

from typing import Any

import pytest
import respx
from httpx import Response
from mcp import types

from beakr_cli.mcp_server import _public_source, mcp
from tests._engine import hosted_render

API = "https://api.example.com"

THREAD = {
    "source_ref": "beakr-source:v1:34000214-5199-4bb8-989c-82dea36aab01",
    "name": "#helios-stability thread 2026-10-22",
    "provider": "slack",
    "location": {"url": "https://northwind.slack.com/archives/C04K10YTMN0/p1742744865524899"},
    "retrieval": {
        "tool": "slack_get_thread",
        "arguments": {"channel_id": "C04K10YTMN0", "thread_ts": "1742744865.524899"},
    },
}
NOTE = {
    "source_ref": "beakr-source:v1:702fc2c7-befd-4ca7-8191-ebb18d18889c",
    "name": "Granola: Helios weekly sync 2026-11-04",
    "provider": "granola",
    "location": {"url": "https://notes.granola.ai/d/0c2f4c1e-5a53-4e0e-a3a0-6b6f6d1c9f11"},
    "retrieval": {"tool": "granola_get_note", "arguments": {"note_id": "not_LiveRf00000001"}},
}
HOSTED_LINES = (
    "- #helios-stability thread 2026-10-22 · source_ref "
    "beakr-source:v1:34000214-5199-4bb8-989c-82dea36aab01 · read with integrations "
    "command='call' tool='slack_get_thread' "
    'arguments={"channel_id": "C04K10YTMN0", "thread_ts": "1742744865.524899"}',
    "- Granola: Helios weekly sync 2026-11-04 · source_ref "
    "beakr-source:v1:702fc2c7-befd-4ca7-8191-ebb18d18889c · read with integrations "
    "command='call' tool='granola_get_note' arguments={\"note_id\": \"not_LiveRf00000001\"}",
)


@pytest.fixture(autouse=True)
def api(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "test-token")


async def _knowledge_base(arguments: dict[str, Any]) -> str:
    handler = mcp._mcp_server.request_handlers[types.CallToolRequest]
    request = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(name="knowledge_base", arguments=arguments),
    )
    result = (await handler(request)).root
    assert not result.isError, result.content
    return result.content[0].text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command", "arguments"),
    [
        ("sections", {"query": "NW-214 lead candidate"}),
        ("sources", {"page": "Compound NW-214"}),
        ("cat", {"page": "Compound NW-214"}),
    ],
)
async def test_a_connected_item_line_names_its_read_call(command: str, arguments: dict) -> None:
    with respx.mock:
        respx.post(f"{API}/v1/knowledge/wiki/command").mock(
            return_value=Response(
                200,
                json={
                    "content": [{"type": "text", "text": "Compound NW-214"}],
                    "sources": [THREAD, NOTE],
                    "outcome": {"status": "success"},
                },
            )
        )
        text = await _knowledge_base({"command": command, "arguments": arguments})

    for line in HOSTED_LINES:
        assert line in text.splitlines()


@pytest.mark.parametrize("mode", ["brief", "full"])
def test_the_sources_list_is_the_hosted_servers(mode: str) -> None:
    records = [_public_source(THREAD), _public_source(NOTE)]

    assert records[1]["retrieval"] == NOTE["retrieval"]
    from beakr_cli.mcp_render import sources_block

    assert sources_block(records, mode=mode) == hosted_render().sources_block(records, mode=mode)
    assert "read with integrations command='call' tool='granola_get_note'" in sources_block(
        records, mode=mode
    )


@pytest.mark.parametrize(
    "retrieval",
    [
        {"tool": "granola_get_note", "arguments": {"note_id": {"nested": "value"}}},
        {"tool": "granola_get_note", "arguments": {}},
        {"tool": "granola_get_note", "arguments": {"note_id": True}},
        {"tool": "", "arguments": {"note_id": "not_abc"}},
        "granola_get_note",
    ],
)
def test_anything_but_a_tool_with_scalar_arguments_is_dropped(retrieval: object) -> None:
    assert "retrieval" not in _public_source({"name": "note", "retrieval": retrieval})


def test_a_file_retrieval_still_carries_only_its_handle() -> None:
    record = _public_source(
        {
            "name": "memo.pdf",
            "retrieval": {
                "tool": "drive_download",
                "arguments": {"handle": "beakr://external/x", "connector_id": "internal"},
            },
        }
    )

    assert record["retrieval"] == {
        "tool": "drive_download",
        "arguments": {"handle": "beakr://external/x"},
    }
