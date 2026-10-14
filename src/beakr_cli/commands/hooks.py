"""``beakr hooks``: start agent sessions with Beakr context and capture them at the end."""

from __future__ import annotations

from typing import Any

import typer

from beakr_cli import agent_hooks
from beakr_cli.output import console, emit, err_console, is_interactive
from beakr_cli.prompts import confirm_if_interactive
from beakr_cli.sdk import get_sdk

app = typer.Typer(
    help="Install agent hooks: Beakr context at session start, session capture as it runs."
)

_CLIENTS = ("claude", "codex", "all")
_client_opt = typer.Option("claude", "--client", "-c", help="claude, codex or all.")
_json_opt = typer.Option(False, "--json", "-j", help="Output JSON.")


def _targets(client: str) -> list[str]:
    if client not in _CLIENTS:
        err_console.print(f"Invalid --client '{client}'. Choose: {', '.join(_CLIENTS)}.")
        raise typer.Exit(2)
    return ["claude", "codex"] if client == "all" else [client]


def _install(target: str) -> dict[str, Any]:
    if target == "claude":
        try:
            path = agent_hooks.install_claude_hooks()
        except agent_hooks.SettingsError as exc:
            err_console.print(f"[red]Claude:[/red] {exc}")
            raise typer.Exit(1) from exc
        return {"client": "claude", "installed": True, "path": str(path)}
    from pathlib import Path

    from beakr_cli.codex_context import write_codex_context
    from beakr_cli.compounding import project_for_session

    cwd = Path.cwd()
    with get_sdk() as sdk:
        result = sdk.engine.context(
            project_id=project_for_session(None, str(cwd)), format="agents_md"
        )
    path = write_codex_context(str(result.get("content") or ""), cwd)
    return {"client": "codex", "installed": True, "path": str(path)}


def _uninstall(target: str) -> dict[str, Any]:
    if target == "claude":
        try:
            removed = agent_hooks.uninstall_claude_hooks()
        except agent_hooks.SettingsError as exc:
            err_console.print(f"[red]Claude:[/red] {exc}")
            raise typer.Exit(1) from exc
        return {
            "client": "claude",
            "removed": removed,
            "path": str(agent_hooks.claude_settings_path()),
        }
    removed = agent_hooks.remove_agents_block()
    return {"client": "codex", "removed": removed, "path": str(agent_hooks.codex_agents_path())}


def hooks_status() -> dict[str, Any]:
    """What is installed where; read-only (``beakr doctor`` uses this)."""
    claude_events = agent_hooks.claude_hooks_installed()
    return {
        "claude": {
            "settings_path": str(agent_hooks.claude_settings_path()),
            "installed": bool(claude_events),
            "events": claude_events,
            "complete": set(claude_events) >= set(agent_hooks.CLAUDE_HOOK_EVENTS),
        },
        "codex": {
            "agents_md": str(agent_hooks.codex_agents_path()),
            "installed": agent_hooks.agents_block_installed(),
        },
    }


@app.command("install")
def install(client: str = _client_opt, json_out: bool = _json_opt) -> None:
    """Install hooks (safe to re-run: existing Beakr hooks are replaced, others untouched)."""
    results = [_install(target) for target in _targets(client)]

    def render(rows: list[dict[str, Any]]) -> None:
        for row in rows:
            if row["client"] == "claude":
                console.print(
                    "[green]Claude:[/green] SessionStart, Stop and SessionEnd hooks "
                    f"in {row['path']}"
                )
                console.print(
                    "        Sessions are captured only in projects that opt in: "
                    "run `beakr hooks capture on` in a bound repository."
                )
            else:
                console.print(f"[green]Codex:[/green]  Beakr context block in {row['path']}")
                console.print(
                    "        Start Codex with `beakr codex` to refresh it before every "
                    "session and capture the session afterwards (if this project "
                    "allows: `beakr hooks capture on`)."
                )

    emit(results, json_out=json_out, render=render)


@app.command("uninstall")
def uninstall(client: str = _client_opt, json_out: bool = _json_opt) -> None:
    """Remove only Beakr's hooks."""
    results = [_uninstall(target) for target in _targets(client)]

    def render(rows: list[dict[str, Any]]) -> None:
        for row in rows:
            label = "Claude:" if row["client"] == "claude" else "Codex: "
            state = "removed from" if row["removed"] else "nothing to remove in"
            console.print(f"{label} {state} {row['path']}")

    emit(results, json_out=json_out, render=render)


@app.command("status")
def status(json_out: bool = _json_opt) -> None:
    """Show which hooks are installed."""
    data = hooks_status()

    def render(info: dict[str, Any]) -> None:
        claude = info["claude"]
        events = ", ".join(claude["events"]) or "none"
        console.print(f"Claude: {events} ({claude['settings_path']})")
        codex_state = "installed" if info["codex"]["installed"] else "not installed"
        console.print(f"Codex:  context block {codex_state} ({info['codex']['agents_md']})")

    emit(data, json_out=json_out, render=render)


@app.command("capture")
def capture(
    state: str = typer.Argument(..., help="on, off or status."),
    json_out: bool = _json_opt,
) -> None:
    """Allow or stop automatic session capture for this repository.

    Installing the hooks is your choice as a user; this is the project's
    choice. Without it, the hooks never upload this repository's sessions.
    A deliberate `beakr compound --from-transcript` still works either way.
    Turning it off also drops this repository's queued retries at the next
    flush, so nothing more is sent.
    """
    from pathlib import Path

    from beakr_cli.project_binding import capture_enabled, find_binding, set_capture

    if state not in ("on", "off", "status"):
        err_console.print("Use `beakr hooks capture on`, `off` or `status`.")
        raise typer.Exit(2)
    cwd = Path.cwd()
    if state != "status":
        try:
            path = set_capture(cwd, state == "on")
        except LookupError as exc:
            err_console.print(str(exc))
            raise typer.Exit(1) from exc
        # Codex reads its capture notice from AGENTS.md, not from a hook run
        # at session start, so a consent change must rewrite it here.
        from beakr_cli.codex_context import sync_capture_notice

        sync_capture_notice()
    else:
        found = find_binding(cwd)
        path = found if found is not None else cwd
    enabled = capture_enabled(cwd)
    result = {"capture": enabled, "binding": str(path)}

    def render(info: dict[str, Any]) -> None:
        word = "[green]on[/green]" if info["capture"] else "off"
        console.print(f"Session capture for this project: {word} ({info['binding']})")

    emit(result, json_out=json_out, render=render)


@app.command("learning")
def learning(
    state: str = typer.Argument(..., help="on, off or status."),
    json_out: bool = _json_opt,
) -> None:
    """Allow or stop personal learning from this repository's captured sessions.

    Separate from `beakr hooks capture`: capture sends sessions to the
    project; learning also lets Beakr learn your preferences from them. Even
    when on, a session is marked interactive only when the agent's own
    transcript shows a person typed in it (Claude Code's terminal entrypoint,
    or an interactive Codex session). Headless runs (`claude -p`, the Agent
    SDK, `codex exec`, subagents) and sessions with no such signal are never
    learned from. Off by default; rebinding to another project turns it off.
    """
    from pathlib import Path

    from beakr_cli.project_binding import find_binding, learning_enabled, set_learning

    if state not in ("on", "off", "status"):
        err_console.print("Use `beakr hooks learning on`, `off` or `status`.")
        raise typer.Exit(2)
    cwd = Path.cwd()
    if state != "status":
        try:
            path = set_learning(cwd, state == "on")
        except LookupError as exc:
            err_console.print(str(exc))
            raise typer.Exit(1) from exc
    else:
        found = find_binding(cwd)
        path = found if found is not None else cwd
    enabled = learning_enabled(cwd)
    result = {"learn_from_sessions": enabled, "binding": str(path)}

    def render(info: dict[str, Any]) -> None:
        word = "[green]on[/green]" if info["learn_from_sessions"] else "off"
        console.print(f"Learning from sessions in this project: {word} ({info['binding']})")

    emit(result, json_out=json_out, render=render)


def install_hooks_during_setup(targets: list[str], hooks_flag: bool | None) -> None:
    """``beakr setup``: True installs, False skips, None asks only when interactive."""
    if hooks_flag is False:
        return
    if hooks_flag is None:
        if not is_interactive():
            return
        if not confirm_if_interactive(
            "Install session hooks (Beakr context at session start, session capture as it runs)?",
            default=True,
        ):
            return
    for target in targets:
        if target not in ("claude", "codex"):
            continue
        try:
            result = _install(target)
        except typer.Exit:
            continue
        except Exception as exc:  # noqa: BLE001 - setup continues past one failed client
            err_console.print(f"[yellow]{target}:[/yellow] hooks not installed ({exc})")
            continue
        console.print(f"[green]{target.capitalize()}:[/green] hooks installed ({result['path']})")


def uninstall_hooks_during_setup(targets: list[str]) -> None:
    """``beakr setup --uninstall``: remove the hooks setup installed, foreign ones untouched."""
    for target in targets:
        if target not in ("claude", "codex"):
            continue
        try:
            result = _uninstall(target)
        except typer.Exit:
            continue
        label = target.capitalize()
        if result["removed"]:
            console.print(f"[green]{label}:[/green] session hooks removed ({result['path']})")


def register(root: typer.Typer) -> None:
    """Attach ``beakr hooks``."""
    root.add_typer(app, name="hooks")
