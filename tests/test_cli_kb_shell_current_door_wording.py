"""``beakr kb`` over the Knowledge door's current wording, as the engine now writes it.

``fixtures/knowledge_door_results.json`` is captured from the engine (see
``tests/test_mcp_knowledge_door_parity.py``), so it holds each read command's
reply in the door's present words. Root cause this guards: the door's wording
changed under the CLI (agent tester report, 2026-11-13) and nothing replayed
the current replies through the terminal rendering, only the older live
capture:

- a page read prints a section's id as ``[section_id=<id>]`` where it printed
  the stored ``<!-- sec:<id> -->`` comment;
- a ``sources`` reply opens ``N distinct source(s), cited M time(s)``;
- a filtered reply opens with a ``Filters:`` line that says a typed value back
  as a JSON string, and a reply over several projects says so and tags each
  page ``[project: <name>]``.

All of that is the reply's content, which ``beakr kb`` prints as the door wrote
it: only ``next`` steps and the lines the reply's records account for become
commands. So every reply here must keep its content, leave no next step in MCP
syntax, and print only commands the CLI has.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from beakr_cli.kb_shell_hints import DoorCall, shell_content, shell_hint, shell_reply
from tests._shell_commands import parse_command, printed_commands
from tests.test_cli_kb_shell_hints import MCP_CALL

READS: dict[str, Any] = json.loads(
    (Path(__file__).parent / "fixtures" / "knowledge_door_results.json").read_text()
)["reads"]


def _call(command: str) -> DoorCall:
    return DoorCall(command, dict(READS[command]["arguments"]))


@pytest.mark.parametrize("command", sorted(READS))
def test_a_current_reply_keeps_its_content_and_prints_real_commands(command: str) -> None:
    body = READS[command]["mcp"]
    call = _call(command)

    text = shell_reply("knowledge_base", body, call)

    for hint in body.get("next") or []:
        assert not MCP_CALL.search(shell_hint(str(hint), call)), hint
    for printed in printed_commands(text):
        parse_command(printed)
    # Without the reply's records, nothing in the content is a line to replace.
    content = str(body.get("content") or "").rstrip()
    assert shell_content(content, call) == content


def test_the_capture_is_in_the_doors_current_wording() -> None:
    cat = READS["cat"]["mcp"]["content"]
    sources = READS["sources"]["mcp"]["content"]

    # Recapture the fixture if these fail: it predates the wording above.
    assert "[section_id=" in cat and "<!-- sec:" not in cat
    assert "distinct source(s), cited" in sources


def test_a_section_marker_in_a_page_read_is_not_read_as_a_continuation() -> None:
    # ``section_id=<id>`` is how a next step names a section to read; inside a
    # page's own text it is the marker of the section that follows, and the
    # page is printed as written.
    call = DoorCall("cat", {"page": "Project Helios"})
    content = "Project Helios\n\n[section_id=sec_charter]\n## Charter\n\nShip by March."
    body = {"content": content, "coverage": {"returned": 1}, "next": []}

    assert shell_reply("knowledge_base", body, call) == content


@pytest.mark.parametrize(
    "line",
    [
        'Filters: changed by "Ali Chen"; changed via "codex" (client or agent prefix); '
        "changed since 2026-11-09 (UTC day).",
        'This reply spans 2 projects: "Helios" (project id 11111111-1111-4111-8111-111111111111), '
        '"Atlas" (project id 22222222-2222-4222-8222-222222222222). A title names one page per '
        "project, so each page says which project it is in; pass a project id as `scope` to "
        "read one project.",
        "  - [topic] Launch Plan [project: Helios]",
        "2 distinct source(s), cited 7 time(s) (one citation per cited passage), for Helios:",
    ],
)
def test_the_doors_notices_are_printed_as_written(line: str) -> None:
    call = DoorCall("ls", {"changed_by": "Ali Chen"})
    content = f"3 matching page(s):\n{line}\n\n  - [topic] Launch Plan"

    assert shell_content(content, call) == content
    assert shell_reply("knowledge_base", {"content": content, "next": []}, call) == content
