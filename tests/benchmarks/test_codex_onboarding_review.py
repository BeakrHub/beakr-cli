"""Context comparisons must keep the common agent instructions identical."""

from beakr_bench.onboarding.agents import CONTEXT_HEADER, SYSTEM_PREAMBLE
from beakr_bench.onboarding.cli_agents import ClaudeCodeAgent


def test_claude_code_arms_share_the_same_non_context_instructions():
    agent = ClaudeCodeAgent()
    prompts = {}
    for arm, context in [("without", None), ("with", "The lab uses metric units.")]:
        argv = agent.command(prompt="Describe our units", context=context, project_id="p1")
        prompts[arm] = (
            argv[argv.index("--append-system-prompt") + 1]
            if "--append-system-prompt" in argv
            else ""
        )
    assert SYSTEM_PREAMBLE in prompts["without"], (
        "Only the context arm gets the instruction to consult team knowledge; "
        "the experiment changes instructions as well as context"
    )
    assert prompts["with"].split(CONTEXT_HEADER)[0].strip() == prompts["without"].strip()
