"""Claude Code and Codex as agents under test, driven headless with the Beakr MCP server.

The spec's bar names these two clients, so the eval drives each the way a team
runs it, with the CLI's own MCP server (``beakr mcp``) as its Beakr tools:

- Claude Code: ``claude -p --output-format stream-json``. Both arms get the
  same preamble through ``--append-system-prompt``; with context, the context
  follows it there, which is where Claude Code's SessionStart hook's
  ``additionalContext`` lands. The context is fetched in ``claude_md``, the
  format that hook asks for.
- Codex: ``codex exec --json`` in a scratch working directory whose
  ``AGENTS.md`` carries the same preamble in both arms; with context, Beakr's
  block follows it exactly as ``beakr hooks install`` writes it
  (``render_agents_block``, ``agents_md`` format), which Codex reads at
  session start.

Every call to a ``beakr`` MCP tool counts as a Knowledge lookup. Token usage and
turns come from each client's own event stream. The parsers accept the event
shapes those clients document for their JSON output; ``tests/benchmarks`` pins
them with recorded streams. A field a client renames degrades a metric to 0
rather than crashing the run, and the first live run should confirm the parsers
against the installed versions (``--keep-transcripts`` keeps raw streams).
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from beakr_bench.onboarding.agents import SYSTEM_PREAMBLE, AgentRun, ToolCall, system_prompt

#: The MCP server name both clients are configured with; tool names carry it.
MCP_SERVER = "beakr"
#: Environment the Beakr MCP server must see: the sandbox credential, the API it
#: points at and the project. Names only; values are never put on the argv.
FORWARDED_ENV = ("BEAKR_API_KEY", "BEAKR_API_URL", "BEAKR_BASE_URL", "BEAKR_PROJECT_ID")

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def run_in_process_group(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    """``subprocess.run`` that kills the agent's whole process tree on timeout.

    ``codex`` is a Node launcher that execs a native binary, and both CLIs start
    an MCP server child. ``subprocess.run`` kills only the direct child on a
    timeout, so a hung run left the native process (and its children) behind,
    one orphan per timed-out run. The agent runs in its own session and the
    whole group is killed instead. The agent never gets the harness's stdin.
    """
    timeout = kwargs.pop("timeout", None)
    kwargs.pop("capture_output", None)
    with subprocess.Popen(
        argv,
        # `codex exec` reads stdin to EOF when it is not a terminal ("Reading
        # additional input from stdin..."); an inherited open stdin (a harness
        # run in the background) made every run wait out its timeout.
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        **kwargs,
    ) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise
    return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)


def raw_events(stdout: str, stderr: str = "") -> list[dict[str, Any]]:
    """Every JSON event a client printed, in order, plus non-JSON lines and stderr's tail.

    The parsers keep only the fields the report needs; the evidence file keeps
    the stream itself, so a run can be reviewed (or re-parsed) later.
    """
    events: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError:
            events.append({"type": "_text", "text": stripped})
            continue
        events.append(value if isinstance(value, dict) else {"type": "_json", "value": value})
    if stderr.strip():
        events.append({"type": "_stderr", "text": stderr[-4000:]})
    return events


def _project_note(prompt: str, project_id: str) -> str:
    # Both arms get the same note, so it is not context: it is where to look.
    return f"{prompt}\n\n(Beakr project for this work: {project_id})"


# -- Claude Code -----------------------------------------------------------------


def parse_claude_stream(stdout: str) -> AgentRun:
    """An ``AgentRun`` from ``claude -p --output-format stream-json --verbose`` output."""
    run = AgentRun()
    prefix = f"mcp__{MCP_SERVER}__"
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = event.get("type")
        if kind == "assistant":
            message = event.get("message") or {}
            if message.get("model"):
                run.models_served.append(str(message["model"]))
            for block in message.get("content") or []:
                name = str(block.get("name") or "")
                if block.get("type") == "tool_use" and name.startswith(prefix):
                    run.tool_calls.append(
                        ToolCall(name.removeprefix(prefix), dict(block.get("input") or {}))
                    )
        elif kind == "result":
            run.answer = str(event.get("result") or "")
            run.turns = int(event.get("num_turns") or 0)
            usage = event.get("usage") or {}
            run.input_tokens = int(usage.get("input_tokens") or 0) + int(
                usage.get("cache_creation_input_tokens") or 0
            )
            run.cache_read_tokens = int(usage.get("cache_read_input_tokens") or 0)
            run.output_tokens = int(usage.get("output_tokens") or 0)
            if event.get("total_cost_usd") is not None:
                run.cost_usd = float(event["total_cost_usd"])
            run.stop_reason = str(event.get("subtype") or "")
            if event.get("is_error"):
                run.error = f"claude reported an error ({run.stop_reason})"
    if not run.answer and run.error is None:
        run.error = "no result event in the stream"
    return run


class ClaudeCodeAgent:
    name = "claude-code"
    context_format = "claude_md"

    def __init__(
        self,
        *,
        model: str | None = None,
        max_turns: int = 8,
        beakr_command: str = "beakr",
        runner: Runner | None = None,
        timeout_s: float = 900.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.model = model
        self.max_turns = max_turns
        self.beakr_command = beakr_command
        self.runner = runner or run_in_process_group
        self.timeout_s = timeout_s
        self.clock = clock

    def command(self, *, prompt: str, context: str | None, project_id: str) -> list[str]:
        mcp = {"mcpServers": {MCP_SERVER: {"command": self.beakr_command, "args": ["mcp"]}}}
        argv = [
            "claude",
            "-p",
            _project_note(prompt, project_id),
            "--output-format",
            "stream-json",
            "--verbose",
            "--max-turns",
            str(self.max_turns),
            "--mcp-config",
            json.dumps(mcp),
            "--strict-mcp-config",
            "--allowedTools",
            f"mcp__{MCP_SERVER}__*",
        ]
        # Both arms carry the same preamble (use team knowledge, look up what you
        # don't know, don't invent facts); only the context block differs, so a
        # difference in outcome measures the context and nothing else (C058).
        argv += ["--append-system-prompt", system_prompt(context)]
        if self.model:
            argv += ["--model", self.model]
        return argv

    def run(self, *, prompt: str, context: str | None, project_id: str) -> AgentRun:
        started = self.clock()
        with tempfile.TemporaryDirectory(prefix="beakr-onboarding-") as workdir:
            try:
                done = self.runner(
                    self.command(prompt=prompt, context=context, project_id=project_id),
                    cwd=workdir,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_s,
                    env={**os.environ, "BEAKR_PROJECT_ID": project_id},
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                return AgentRun(error=f"{type(exc).__name__}: {exc}", wall_s=0.0)
        run = parse_claude_stream(done.stdout or "")
        run.transcript = raw_events(done.stdout or "", done.stderr or "")
        if done.returncode and run.error is None:
            run.error = f"claude exited {done.returncode}: {(done.stderr or '')[-300:]}"
        run.wall_s = round(self.clock() - started, 3)
        return run


# -- Codex -----------------------------------------------------------------------


def parse_codex_events(stdout: str) -> AgentRun:
    """An ``AgentRun`` from ``codex exec --json`` (JSONL thread events)."""
    run = AgentRun()
    messages: list[str] = []
    pending_errors: list[str] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = event.get("type")
        item = event.get("item") or {}
        if kind == "item.completed" and item.get("type") == "mcp_tool_call":
            if item.get("server") == MCP_SERVER:
                run.tool_calls.append(
                    ToolCall(
                        str(item.get("tool") or ""),
                        dict(item.get("arguments") or {}),
                        ok=item.get("status") != "failed",
                    )
                )
        elif kind == "item.completed" and item.get("type") == "agent_message":
            messages.append(str(item.get("text") or ""))
        elif kind == "turn.completed":
            run.turns += 1
            usage = event.get("usage") or {}
            run.input_tokens += int(usage.get("input_tokens") or 0)
            run.cache_read_tokens += int(usage.get("cached_input_tokens") or 0)
            run.output_tokens += int(usage.get("output_tokens") or 0)
        elif kind == "turn.failed":
            error = event.get("error") or {}
            run.error = str(error.get("message") if isinstance(error, dict) else error or kind)
        elif kind == "error":
            # Codex also reports warnings as top-level "error" events (missing
            # model metadata, shortened skill descriptions) and then carries on.
            # One counts as the run's error only if no turn completes.
            pending_errors.append(str(event.get("message") or event.get("error") or kind))
    if run.error is None and run.turns == 0 and pending_errors:
        run.error = pending_errors[-1]
    run.answer = messages[-1] if messages else ""
    if not run.answer and run.error is None:
        run.error = "no agent message in the event stream"
    return run


class CodexAgent:
    name = "codex"
    context_format = "agents_md"

    def __init__(
        self,
        *,
        model: str | None = None,
        beakr_command: str = "beakr",
        runner: Runner | None = None,
        timeout_s: float = 900.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.model = model
        self.beakr_command = beakr_command
        self.runner = runner or run_in_process_group
        self.timeout_s = timeout_s
        self.clock = clock

    def command(self, *, prompt: str, project_id: str, workdir: str) -> list[str]:
        argv = [
            "codex",
            "exec",
            "--json",
            "--skip-git-repo-check",
            "-C",
            workdir,
            "-c",
            f"mcp_servers.{MCP_SERVER}.command={json.dumps(self.beakr_command)}",
            "-c",
            f'mcp_servers.{MCP_SERVER}.args=["mcp"]',
            # Headless `codex exec` cannot answer an approval prompt, and its
            # default policy refuses every MCP tool call ("blocked by the
            # current approval policy"), so the Beakr tools must be approved
            # up front or neither arm can look anything up.
            "-c",
            f'mcp_servers.{MCP_SERVER}.default_tools_approval_mode="approve"',
            # Codex starts MCP servers with a scrubbed environment. Without
            # forwarding these, `beakr mcp` falls back to the stored login
            # (~/.beakr) and queries whatever org that is -- in a live run, the
            # person's production organization instead of the sandbox.
            "-c",
            f"mcp_servers.{MCP_SERVER}.env_vars={json.dumps(list(FORWARDED_ENV))}",
        ]
        if self.model:
            argv += ["-m", self.model]
        argv.append(_project_note(prompt, project_id))
        return argv

    def run(self, *, prompt: str, context: str | None, project_id: str) -> AgentRun:
        from beakr_cli.agent_hooks import render_agents_block

        started = self.clock()
        workdir = tempfile.mkdtemp(prefix="beakr-onboarding-")
        try:
            # The same preamble in both arms; Beakr's block only with context (C058).
            agents_md = SYSTEM_PREAMBLE + "\n"
            if context:
                agents_md += "\n" + render_agents_block(context) + "\n"
            Path(workdir, "AGENTS.md").write_text(agents_md)
            try:
                done = self.runner(
                    self.command(prompt=prompt, project_id=project_id, workdir=workdir),
                    cwd=workdir,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_s,
                    env={**os.environ, "BEAKR_PROJECT_ID": project_id},
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                return AgentRun(error=f"{type(exc).__name__}: {exc}")
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        run = parse_codex_events(done.stdout or "")
        run.transcript = raw_events(done.stdout or "", done.stderr or "")
        if done.returncode and run.error is None:
            run.error = f"codex exited {done.returncode}: {(done.stderr or '')[-300:]}"
        run.wall_s = round(self.clock() - started, 3)
        return run


def require_binary(name: str) -> None:
    if shutil.which(name) is None:
        raise SystemExit(f"The {name} agent needs `{name}` on PATH.")


def describe(agent: Any) -> dict[str, Any]:
    return {"agent": agent.name, "model": getattr(agent, "model", None)}


__all__ = [
    "ClaudeCodeAgent",
    "CodexAgent",
    "describe",
    "parse_claude_stream",
    "parse_codex_events",
    "raw_events",
    "require_binary",
    "run_in_process_group",
]
