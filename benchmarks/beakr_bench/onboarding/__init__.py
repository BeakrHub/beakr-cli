"""Agent onboarding eval: does Beakr's injected context make an outside agent better and cheaper?

The developer platform's evaluation bar for agent onboarding is a harness eval
in which the same tasks run with and without ``context`` injection, in Claude
Code and Codex, and the bar is *higher task success and fewer Knowledge lookups
with context*.

Each case seeds a fresh sandbox project through the public API, then runs its
task twice with the same agent:

- **with context**: the agent starts with what Beakr's session hooks inject
  (``POST /v1/context`` for the project, in the format that agent's hook uses:
  ``claude_md`` for Claude Code's SessionStart hook, ``agents_md`` for the Codex
  AGENTS.md block, ``markdown`` for the API agent);
- **without context**: the agent starts cold.

Both arms have the same Beakr tools (the CLI's MCP server, or the equivalent
SDK-backed tools for the API agent), so the without-context arm can still look
things up; every Beakr tool call is counted as a Knowledge lookup.

Agents (``agents.py``, ``cli_agents.py``):

- ``api``: a manual tool-use loop on the Anthropic Messages API (the reference
  agent; reproducible, and it reports exact token usage);
- ``claude-code``: ``claude -p`` with the CLI's MCP server, context appended to
  the system prompt the way the SessionStart hook adds it;
- ``codex``: ``codex exec`` with the CLI's MCP server, context written to the
  working directory's AGENTS.md exactly as ``beakr hooks install`` writes it;
- ``scripted``: an offline stand-in model behind the real ``api`` loop, for the
  dry run and CI. It says nothing about any model or about Beakr.

Scoring reuses ``beakr_bench.scoring`` (deterministic checks on the final answer).
"""

from __future__ import annotations

SUITE = "agent_onboarding"
ARMS = ("with_context", "without_context")
AGENTS = ("api", "claude-code", "codex", "scripted")
