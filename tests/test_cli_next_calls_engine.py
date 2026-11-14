"""``next_calls`` as the engine ships them, through the CLI's rendering.

The fixture ``tests/fixtures/engine_next_calls.json`` is the engine's own step
tables run through its ``build_next_calls`` (``scripts/capture_engine_next_calls.py``,
run against the engine branch). As shipped, ``next_calls`` is NOT aligned with
``next``: one sentence can give several calls, a sentence that names none
gives none, the continuation comes first, and repeats are dropped. These tests
hold the CLI to that: a prose line is dropped only when it names a call (by the
engine's own rule), wherever it sits, and every call becomes a command that
parses, or its why alone.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from beakr_cli.kb_shell_hints import DoorCall, shell_hint, shell_reply, with_cursor
from beakr_cli.kb_shell_next import names_a_call, next_steps
from beakr_cli.terminal_text import visible
from tests._shell_commands import parse_command

CAPTURED: dict[str, Any] = json.loads(
    (Path(__file__).parent / "fixtures" / "engine_next_calls.json").read_text()
)
REPLIES = CAPTURED["replies"]
IDS = [reply["name"] for reply in REPLIES]


def _call(reply: dict[str, Any]) -> DoorCall:
    request = dict(reply["request"])
    return DoorCall(str(request.pop("command", "research")), request)


def test_the_fixture_is_not_index_aligned() -> None:
    # What these tests exist to cover: several calls per sentence, sentences with none.
    assert any(len(r["next"]) != len(r["next_calls"]) for r in REPLIES)
    assert any(
        r["next_calls"] and r["next_calls"][0]["why"].startswith("Read the rest") for r in REPLIES
    )


@pytest.mark.parametrize("reply", REPLIES, ids=IDS)
def test_a_sentence_names_a_call_by_the_engines_rule(reply: dict[str, Any]) -> None:
    for hint, verdict in zip(reply["next"], reply["names_a_call"], strict=True):
        assert names_a_call(hint) is verdict, hint


@pytest.mark.parametrize(
    "reply", [r for r in REPLIES if r["tool"] == "knowledge_base"], ids=lambda r: r["name"]
)
def test_each_captured_reply_prints_its_calls_and_only_the_prose_that_names_none(
    reply: dict[str, Any],
) -> None:
    call = _call(reply)
    body = {
        "content": "The reply's text.",
        "coverage": reply["coverage"],
        "next": reply["next"],
        "next_calls": reply["next_calls"],
    }

    output = visible(shell_reply("knowledge_base", body, call))

    lines = output.split("\n")
    if not reply["next_calls"]:
        # No sentence names a call: the prose is printed as before, with no block.
        assert "Next:" not in lines
        for hint in reply["next"]:
            assert f"next: {shell_hint(hint, call)}" in output, hint
        return
    assert "Next:" in lines, output
    block = lines[lines.index("Next:") + 1 :]
    above = "\n".join(lines[: lines.index("Next:")])
    # Every sentence that names a call is replaced by the block, wherever it sat;
    # every one that names none is still printed.
    for hint, verdict in zip(reply["next"], reply["names_a_call"], strict=True):
        if verdict:
            assert f"next: {shell_hint(hint, call)}" not in above, hint
        else:
            assert f"next: {shell_hint(hint, call)}" in above, hint
    # Every call is a command that parses, or its why alone; none is lost.
    steps = next_steps(reply["next_calls"], with_cursor(call, reply["coverage"])) or []
    assert len(steps) == len(reply["next_calls"])
    for step in steps:
        if step.command is None:
            assert f"  - {step.why}" in block, step
            continue
        parse_command(step.command)
        assert any(line.startswith(f"  {step.command}") for line in block), (step, block)
    # The continuation, when there is one, comes first, as the engine orders it.
    if reply["next_calls"] and reply["next_calls"][0]["why"].startswith("Read the rest"):
        first = next(line for line in block if line.startswith("  "))
        assert first.startswith(f"  {steps[0].command}"), block


def test_the_research_reply_prints_its_deep_and_save_steps_as_commands() -> None:
    from beakr_cli.kb_shell_hints import next_lines

    reply = next(r for r in REPLIES if r["tool"] == "research")
    prose, block = next_lines(
        {"next": reply["next"], "next_calls": reply["next_calls"]},
        DoorCall("research", {"scope": None}),
        tool="research",
    )

    commands = [line.split("    (")[0].strip() for line in block if line.startswith("  beakr ")]
    assert any(command.startswith("beakr research ") for command in commands), block
    assert any(command.startswith("beakr kb add-finding ") for command in commands), block
    for command in commands:
        parse_command(command)
    for hint, verdict in zip(reply["next"], reply["names_a_call"], strict=True):
        assert (hint in prose) is not verdict
