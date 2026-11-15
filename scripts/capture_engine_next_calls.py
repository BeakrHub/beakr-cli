"""Capture the engine's own ``next`` steps and ``next_calls`` as a CLI test fixture.

Run inside a beakr-engine checkout's environment (it imports the engine):

    cd <beakr-engine> && ENV=test PYTHONPATH=. uv run python \
        <beakr-cli>/scripts/capture_engine_next_calls.py \
        > <beakr-cli>/tests/fixtures/engine_next_calls.json

For every step table the engine's routes use (the same tables its
``tests/api/test_next_calls_http.py`` walks), plus each continuation shape and a
cut ``cat``, it records the reply's ``next`` and the ``next_calls``
``build_next_calls`` gives it for a request in project ``Alpha``, and the engine's
``names_a_call`` verdict on each sentence. Nothing is
written to the engine.
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any


def main() -> int:
    from beakr.engines.agents.research.quick import QUICK_NEXT_HINT
    from beakr.engines.tools.graph import kb_result_coverage as coverage_module
    from beakr.engines.tools.graph.kb_read_v2.cat_address import CatAddress, cut_report
    from beakr.engines.tools.graph.kb_read_v2.grep_related import RELATED_NEXT
    from beakr.engines.tools.graph.kb_read_v2.identifier_query import grep_hint
    from beakr.engines.tools.graph.next_calls import build_next_calls, names_a_call
    from beakr.services.public_answers import WRITE_BACK_HINT

    replies: list[dict[str, Any]] = []

    def add(name: str, hints: list[Any], request: dict[str, Any], **kwargs: Any) -> None:
        calls = build_next_calls(hints, request=request, **kwargs)
        replies.append(
            {
                "name": name,
                "request": request,
                "coverage": kwargs.get("coverage"),
                "tool": kwargs.get("tool", "knowledge_base"),
                "next": [str.__str__(hint) for hint in hints],
                # The engine's own verdict on each sentence: whether it names a call.
                "names_a_call": [names_a_call(hint) for hint in hints],
                "next_calls": calls,
            }
        )

    for table in ("_NEXT_ON_SUCCESS", "_OUTSIDE_NEXT_ON_SUCCESS", "_NEXT_ON_EMPTY"):
        for command, hints in getattr(coverage_module, table).items():
            add(f"{table}[{command}]", list(hints), {"command": command, "scope": "Alpha"})
    for table in ("_NEXT_ON_EMPTY_DEFAULT", "_NEXT_ON_ERROR", "_OUTSIDE_DEFAULT_NEXT"):
        add(table, list(getattr(coverage_module, table)), {"command": "grep", "scope": "Alpha"})
    add("RELATED_NEXT", list(RELATED_NEXT), {"command": "grep", "query": "x", "scope": "Alpha"})
    add("grep_hint", [grep_hint("ENG-2341")], {"command": "grep", "query": "ENG-2341"})
    add("QUICK_NEXT_HINT", [QUICK_NEXT_HINT, WRITE_BACK_HINT], {"query": "q"}, tool="research")
    for command, coverage in (
        ("log", {"complete": False, "next_offset": 20}),
        ("rows", {"complete": False, "next_cursor": "c1"}),
        ("timeline", {"complete": False, "next_cursor": "t1"}),
        ("ls", {"complete": False, "continue_with": {"after_title": 'A "quoted" title'}}),
        ("sources", {"complete": False, "continue_with": {"after_title": "T"}}),
    ):
        hint = coverage_module.continuation_hint(command, coverage)
        extra = list(coverage_module._NEXT_ON_SUCCESS.get(command, ()))
        request = {"command": command, "scope": "Alpha", "limit": 5}
        if command == "sources":
            request["page"] = "Helios"
        add(f"continuation[{command}]", [*extra, hint], request, coverage=coverage)
    report = cut_report(
        shown_chars=100,
        total_chars=900,
        address=CatAddress(
            page_id="9b445bea-66bc-43a0-a651-8eb1c77bcb3f",
            revision=3,
            first_line=1,
            line_count=40,
            tail_newlines=0,
        ),
        lines_shown=4,
        sections_not_shown=["sec_facts", "sec_connections"],
        page_argument="Helios",
        scope="Alpha",
    )
    add(
        "cut_cat",
        [report.hint, *coverage_module._NEXT_ON_SUCCESS["cat"]],
        {"command": "cat", "page": "Helios", "scope": "Alpha"},
        coverage=report.coverage,
    )
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    ).stdout.strip()
    captured = {"engine_commit": commit, "replies": replies}
    json.dump(captured, sys.stdout, indent=1, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
