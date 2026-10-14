"""Install Beakr's session hooks into Claude Code settings and Codex's AGENTS.md.

Claude Code (``<claude home>/settings.json``):

- ``SessionStart`` runs ``beakr context --format claude_md --project auto --hook``,
  whose JSON output Claude Code injects as context;
- ``Stop`` runs ``beakr compound --hook ... --background`` after each turn. The
  compound command throttles per session, because Stop fires every turn;
- ``SessionEnd`` runs the same with ``--force`` so the final state of a session
  is always captured, whatever the throttle skipped.

Beakr's entries are recognised by their subcommand text, so installing twice
changes nothing, other tools' hooks are never touched, and uninstall removes
only what Beakr added. An unparseable settings file is refused, never rewritten.

Codex has no session hooks here; it reads ``<codex home>/AGENTS.md``, so the
context is rendered into a marked block there (with a capture notice, see
``beakr_cli.codex_context``), and past sessions are imported with
``beakr compound --from-codex-sessions``.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any

from beakr_cli.commands.install import _beakr_command, _claude_home, _codex_home

HOOK_TIMEOUT_SECONDS = 30

CONTEXT_ARGS = "context --format claude_md --project auto --hook"
STOP_ARGS = "compound --hook --client claude-code --background"
SESSION_END_ARGS = "compound --hook --client claude-code --background --force"

#: Event -> the beakr arguments its hook runs.
CLAUDE_HOOK_EVENTS: dict[str, str] = {
    "SessionStart": CONTEXT_ARGS,
    "Stop": STOP_ARGS,
    "SessionEnd": SESSION_END_ARGS,
}

#: Substrings that mark a hook command as Beakr's own (any binary path).
_OWN_COMMAND_MARKERS = (" context --format claude_md", " compound --hook")

AGENTS_START = "<!-- beakr:context:start -->"
AGENTS_END = "<!-- beakr:context:end -->"


class SettingsError(Exception):
    """The settings file exists but cannot be safely edited."""


def claude_settings_path() -> Path:
    return _claude_home() / "settings.json"


def codex_agents_path() -> Path:
    return _codex_home() / "AGENTS.md"


def _beakr_invocation(args: str) -> str:
    return f"{shlex.quote(_beakr_command())} {args}"


def is_beakr_hook_command(command: Any) -> bool:
    return isinstance(command, str) and any(
        marker in f" {command}" for marker in _OWN_COMMAND_MARKERS
    )


# ---------------------------------------------------------------------------
# Claude Code settings.json
# ---------------------------------------------------------------------------


def _load_settings(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    text = path.read_text()
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise SettingsError(
            f"{path} is not valid JSON ({exc}); fix it, then run this again. It was left unchanged."
        ) from exc
    if not isinstance(data, dict):
        raise SettingsError(f"{path} is not a JSON object; it was left unchanged.")
    hooks = data.get("hooks")
    if hooks is not None and not isinstance(hooks, dict):
        raise SettingsError(f"'hooks' in {path} is not an object; it was left unchanged.")
    return data


def _save_settings(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def _strip_own_hooks(groups: list[Any]) -> list[Any]:
    """Remove Beakr's commands from matcher groups; drop groups left empty."""
    kept: list[Any] = []
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
            kept.append(group)
            continue
        remaining = [
            hook
            for hook in group["hooks"]
            if not (isinstance(hook, dict) and is_beakr_hook_command(hook.get("command")))
        ]
        if remaining:
            kept.append({**group, "hooks": remaining})
        elif not group["hooks"]:
            # An empty group someone else wrote is theirs to keep.
            kept.append(group)
    return kept


def install_claude_hooks() -> Path:
    """Add (or refresh) Beakr's three hooks; idempotent."""
    path = claude_settings_path()
    data = _load_settings(path)
    hooks = data.setdefault("hooks", {})
    for event, args in CLAUDE_HOOK_EVENTS.items():
        groups = hooks.get(event)
        groups = _strip_own_hooks(groups if isinstance(groups, list) else [])
        entry: dict[str, Any] = {
            "hooks": [
                {
                    "type": "command",
                    "command": _beakr_invocation(args),
                    "timeout": HOOK_TIMEOUT_SECONDS,
                }
            ]
        }
        if event == "SessionStart":
            entry = {"matcher": "", **entry}
        groups.append(entry)
        hooks[event] = groups
    _save_settings(path, data)
    return path


def uninstall_claude_hooks() -> bool:
    """Remove only Beakr's hooks. Returns whether anything was removed."""
    path = claude_settings_path()
    if not path.exists():
        return False
    data = _load_settings(path)
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return False
    before = json.dumps(hooks, sort_keys=True)
    for event in list(hooks):
        groups = hooks[event]
        if not isinstance(groups, list):
            continue
        stripped = _strip_own_hooks(groups)
        if stripped:
            hooks[event] = stripped
        else:
            del hooks[event]
    if json.dumps(hooks, sort_keys=True) == before:
        return False
    if not hooks:
        del data["hooks"]
    _save_settings(path, data)
    return True


def claude_hooks_installed() -> list[str]:
    """Events that currently run a Beakr hook (empty if none or unreadable)."""
    try:
        data = _load_settings(claude_settings_path())
    except SettingsError:
        return []
    events = []
    for event, groups in (data.get("hooks") or {}).items():
        if not isinstance(groups, list):
            continue
        for group in groups:
            hooks = group.get("hooks") if isinstance(group, dict) else None
            if isinstance(hooks, list) and any(
                isinstance(hook, dict) and is_beakr_hook_command(hook.get("command"))
                for hook in hooks
            ):
                events.append(event)
                break
    return events


# ---------------------------------------------------------------------------
# Codex AGENTS.md
# ---------------------------------------------------------------------------


def render_agents_block(content: str) -> str:
    return f"{AGENTS_START}\n{content.strip()}\n{AGENTS_END}"


def _split_block(text: str) -> tuple[str, str] | None:
    """Text before and after the Beakr block, or None when there is no block."""
    start = text.find(AGENTS_START)
    end = text.find(AGENTS_END, start + 1) if start != -1 else -1
    if start == -1 or end == -1:
        return None
    return text[:start], text[end + len(AGENTS_END) :]


def write_agents_block(content: str) -> Path:
    """Replace the block between the markers, or append it; the rest is preserved."""
    path = codex_agents_path()
    existing = path.read_text() if path.exists() else ""
    block = render_agents_block(content)
    parts = _split_block(existing)
    if parts is not None:
        updated = parts[0] + block + parts[1]
    elif existing.strip():
        updated = existing.rstrip("\n") + "\n\n" + block + "\n"
    else:
        updated = block + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(updated)
    return path


def remove_agents_block() -> bool:
    path = codex_agents_path()
    if not path.exists():
        return False
    parts = _split_block(path.read_text())
    if parts is None:
        return False
    before, after = parts
    remaining = (before.rstrip("\n") + "\n" + after.lstrip("\n")).strip("\n")
    if remaining:
        path.write_text(remaining + "\n")
    else:
        path.unlink()
    return True


def read_agents_block() -> str | None:
    """The content between the markers, or None when there is no block."""
    path = codex_agents_path()
    if not path.exists():
        return None
    text = path.read_text()
    start = text.find(AGENTS_START)
    end = text.find(AGENTS_END, start + 1) if start != -1 else -1
    if start == -1 or end == -1:
        return None
    return text[start + len(AGENTS_START) : end]


def agents_block_installed() -> bool:
    path = codex_agents_path()
    return path.exists() and _split_block(path.read_text()) is not None
