"""The Beakr block in Codex's ``AGENTS.md``: engine context plus a visible capture notice.

Claude Code's SessionStart hook appends a line saying whether the session will
be captured (spec step 8.1 item 3). Codex has no hooks, so the same line lives
in the AGENTS.md block that ``beakr setup``, ``beakr hooks install --client
codex`` and the ``beakr codex`` wrapper write.

``AGENTS.md`` is global (``~/.codex/AGENTS.md``) while consent is per
repository, so the block records which directory and project it was written
for, and the notice names that repository. The notice is always computed from
local consent state, never from the engine, which keeps it correct when:

- the remote context refresh fails (the notice is rewritten anyway, and engine
  facts that belong to another project are dropped rather than kept);
- consent is withdrawn or granted (``beakr hooks capture on|off``);
- the repository is rebound to another project (``beakr init``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rich.markup import escape

from beakr_cli import agent_hooks
from beakr_cli.project_binding import (
    CAPTURE_FIELD,
    binding_applies,
    find_binding,
    read_binding,
)

_META_PREFIX = "<!-- beakr:context:meta "
_META_SUFFIX = " -->"
NOTICE_START = "<!-- beakr:capture-notice:start -->"
NOTICE_END = "<!-- beakr:capture-notice:end -->"


@dataclass(frozen=True)
class CodexBlock:
    """What a written Beakr block says about itself."""

    directory: str | None
    project_id: str | None
    facts: str


def _repository_root(directory: Path) -> Path:
    binding_path = find_binding(directory)
    return binding_path.parent.parent if binding_path is not None else directory


def capture_notice(directory: Path) -> str:
    """The capture line for sessions started in ``directory``, from local state only."""
    from beakr_cli.project_binding import capture_enabled

    directory = directory.resolve()
    binding = read_binding(directory)
    repository = _repository_root(directory)
    if not binding or not binding.get("project_id"):
        return (
            f"Beakr session capture is off: {repository} is not bound to a Beakr project, "
            "so Codex sessions started there are not imported. To capture them, run "
            "`beakr init` and then `beakr hooks capture on` in that repository."
        )
    project = binding.get("name") or binding.get("project_id")
    if capture_enabled(directory):
        return (
            f"Beakr session capture is on for {repository} (project {project}): Codex "
            "sessions started there with `beakr codex` are imported into your private "
            "Beakr conversations after they end. Turn it off with `beakr hooks capture off` "
            "in that repository."
        )
    if binding.get(CAPTURE_FIELD) is True and not binding_applies(binding):
        reason = (
            "its binding was made for a different Beakr server or organization than the "
            "one you are signed in to"
        )
    else:
        reason = "the repository has not opted in"
    return (
        f"Beakr session capture is off for {repository} (project {project}): {reason}, so "
        "Codex sessions started there are not imported. To capture them, run "
        "`beakr hooks capture on` in that repository and start Codex with `beakr codex`."
    )


def render_block(facts: str, *, directory: Path, project_id: str | None) -> str:
    """The block's inner content: metadata line, engine facts, then the notice."""
    meta = json.dumps(
        {"directory": str(directory.resolve()), "project_id": project_id}, sort_keys=True
    )
    parts = [f"{_META_PREFIX}{meta}{_META_SUFFIX}"]
    if facts.strip():
        parts.append(facts.strip())
    parts.append(f"{NOTICE_START}\n{capture_notice(directory)}\n{NOTICE_END}")
    return "\n\n".join(parts)


def read_block() -> CodexBlock | None:
    """The current block, or None when none is installed."""
    content = agent_hooks.read_agents_block()
    if content is None:
        return None
    directory: str | None = None
    project_id: str | None = None
    lines = content.strip().splitlines()
    if lines and lines[0].startswith(_META_PREFIX) and lines[0].endswith(_META_SUFFIX):
        raw = lines[0][len(_META_PREFIX) : -len(_META_SUFFIX)]
        try:
            meta: Any = json.loads(raw)
        except ValueError:
            meta = {}
        if isinstance(meta, dict):
            directory = meta.get("directory") if isinstance(meta.get("directory"), str) else None
            project_id = meta.get("project_id") if isinstance(meta.get("project_id"), str) else None
        lines = lines[1:]
    facts = "\n".join(lines)
    start = facts.find(NOTICE_START)
    end = facts.find(NOTICE_END, start + 1) if start != -1 else -1
    if start != -1 and end != -1:
        facts = facts[:start] + facts[end + len(NOTICE_END) :]
    return CodexBlock(directory=directory, project_id=project_id, facts=facts.strip())


def _project_for(directory: Path) -> str | None:
    from beakr_cli.compounding import project_for_session

    return project_for_session(None, str(directory))


def write_codex_context(facts: str, directory: Path) -> Path:
    """Write fresh engine facts for ``directory`` with its current capture notice."""
    return agent_hooks.write_agents_block(
        render_block(facts, directory=directory, project_id=_project_for(directory))
    )


def _unavailable_facts(project_id: str | None, reason: str) -> str:
    target = f"project {project_id}" if project_id else "this directory"
    return (
        f"Beakr context for {target} could not be loaded ({reason}). "
        "Use the Beakr MCP tools, or run `beakr codex` again to refresh it."
    )


def rewrite_from_local_state(directory: Path, *, reason: str, create: bool) -> Path | None:
    """Rewrite the block for ``directory`` without the engine.

    Engine facts already in the block are kept only when they were written for
    the same directory and the same project; otherwise they describe something
    else and are replaced by a short "could not be loaded" line. The notice is
    always recomputed. Returns None when there is no block and ``create`` is off.
    """
    current = read_block()
    if current is None and not create:
        return None
    directory = directory.resolve()
    project_id = _project_for(directory)
    same_target = (
        current is not None
        and current.directory == str(directory)
        and current.project_id == project_id
        and bool(current.facts)
    )
    facts = current.facts if same_target and current is not None else ""
    if not facts:
        facts = _unavailable_facts(project_id, reason)
    return agent_hooks.write_agents_block(
        render_block(facts, directory=directory, project_id=project_id)
    )


def sync_capture_notice() -> Path | None:
    """After a local consent or binding change, bring the installed block up to date.

    The block describes the directory it was last written for, so that
    directory's consent and binding are re-read, whichever repository changed.
    A block written before notices existed records no directory and is left
    for the next refresh. Never contacts the engine.
    """
    from beakr_cli.hook_runtime import log_hook
    from beakr_cli.output import err_console

    try:
        current = read_block()
        if current is None or current.directory is None:
            return None
        return rewrite_from_local_state(
            Path(current.directory),
            reason="its project binding changed since it was written",
            create=False,
        )
    except OSError as exc:
        # The consent change itself is already saved; say the notice is behind.
        log_hook(f"codex notice: could not rewrite {agent_hooks.codex_agents_path()}: {exc}")
        err_console.print(
            f"[yellow]Beakr:[/yellow] could not update the capture notice in "
            f"{escape(str(agent_hooks.codex_agents_path()))} ({escape(str(exc))}); run `beakr "
            "codex` to refresh it."
        )
        return None
