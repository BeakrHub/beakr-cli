"""conversations_search says how well each hit matches, and warns when none is strong.

ROOT CAUSE (agent tool review, 2026-11-06): conversation search ranked by
``ts_rank``, which has no calibrated scale, and printed every hit alike, so an
agent read a hit that shared one word with the query as an answer. The engine
now gives each hit ``match`` (strong, moderate, tangential or weak, from term
coverage and phrase) and the page ``match_strength`` and, when no hit is strong,
a ``note``; the hosted MCP tool prints the note first and the label on each hit
(``formatting.conversation_hits``). The stdio tool prints the same, and the
same text as before against an engine without the fields.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

import httpx
import pytest
import respx

from beakr_cli.mcp_tools_platform import conversation_hits_text, conversations_search
from tests._engine import real_engine

API = "https://api.example.com"
NOTE = "No strong matches: these conversations may not cover the question."


@pytest.fixture(autouse=True)
def api(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "test-token")


def _hits(*, labelled: bool) -> list[dict[str, Any]]:
    hits = [
        {
            "kind": "imported",
            "id": "cv1",
            "title": "Pump calibration",
            "summary": "Chose the March curve",
            "updated_at": "2026-10-01T00:00",
            "findings": [{"page_title": "Pump calibration"}],
            "outputs": [],
            "compound_status": "partial",
        },
        {
            "kind": "ask_thread",
            "id": "t1",
            "title": "Pump curves",
            "updated_at": "2026-09-30T00:00",
            "findings": [],
            "outputs": [{"title": "Curve memo"}],
        },
    ]
    if labelled:
        hits[0]["match"] = "moderate"
        hits[1]["match"] = "tangential"
    return hits


async def _search(page: dict[str, Any]) -> str:
    with respx.mock:
        respx.get(f"{API}/v1/conversations/search").mock(
            return_value=httpx.Response(200, json=page)
        )
        return (await conversations_search(query="which pump curve"))["content"]


@pytest.mark.asyncio
async def test_the_note_comes_first_and_each_hit_says_how_well_it_matches() -> None:
    text = await _search({"data": _hits(labelled=True), "match_strength": "moderate", "note": NOTE})
    lines = text.splitlines()
    assert lines[:2] == [NOTE, ""]
    assert "- Pump calibration (imported session, 2026-10-01, moderate match, id: cv1)" in lines
    assert "- Pump curves (Beakr chat, 2026-09-30, tangential match, id: t1)" in lines


@pytest.mark.asyncio
async def test_an_engine_without_match_labels_prints_what_it_printed_before() -> None:
    text = await _search({"data": _hits(labelled=False)})
    assert text.splitlines()[0] == ("- Pump calibration (imported session, 2026-10-01, id: cv1)")
    assert "match" not in text


_HOSTED = """\
import inspect, json, sys
from beakr.api.mcp_remote import formatting
if "note" not in inspect.signature(formatting.conversation_hits).parameters:
    print(json.dumps(None))
    raise SystemExit
cases = json.load(sys.stdin)
print(json.dumps([formatting.conversation_hits(c["hits"], note=c["note"]) for c in cases]))
"""


def test_the_stdio_hits_are_the_hosted_ones_byte_for_byte() -> None:
    engine = real_engine("beakr/api/mcp_remote/formatting.py")
    cases = [
        {"hits": _hits(labelled=True), "note": NOTE},
        {"hits": _hits(labelled=True), "note": None},
        {"hits": _hits(labelled=False), "note": None},
        {"hits": [], "note": NOTE},
    ]
    hosted = subprocess.run(
        [str(engine / ".venv/bin/python"), "-c", _HOSTED],
        cwd=engine,
        input=json.dumps(cases),
        text=True,
        capture_output=True,
        check=True,
    )
    expected = json.loads(hosted.stdout)
    if expected is None:
        pytest.skip("this engine checkout predates conversation search match labels")
    assert [conversation_hits_text(c["hits"], note=c["note"]) for c in cases] == expected
