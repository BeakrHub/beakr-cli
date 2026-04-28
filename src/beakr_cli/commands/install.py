"""Install Beakr skills and slash commands into Claude Code / Codex."""

from __future__ import annotations

import importlib.resources
import shutil
from enum import Enum
from pathlib import Path

import typer

from beakr_cli.output import console, err_console


class Client(str, Enum):
    auto = "auto"
    claude = "claude"
    codex = "codex"
    all = "all"


class Scope(str, Enum):
    user = "user"
    project = "project"


def _assets_root() -> Path:
    return Path(str(importlib.resources.files("beakr_cli") / "assets"))


def _skill_src() -> Path:
    return _assets_root() / "skills" / "beakr"


def _commands_src() -> Path:
    return _assets_root() / "commands"


def _detect_clients() -> list[str]:
    found: list[str] = []
    if (Path.home() / ".claude").exists():
        found.append("claude")
    if (Path.home() / ".codex").exists():
        found.append("codex")
    return found


def _resolve_targets(client: Client) -> list[str]:
    if client is Client.auto:
        return _detect_clients()
    if client is Client.all:
        return ["claude", "codex"]
    return [client.value]


def _claude_paths(scope: Scope) -> tuple[Path, Path]:
    base = Path.home() / ".claude" if scope is Scope.user else Path.cwd() / ".claude"
    return base / "skills" / "beakr", base / "commands"


def _codex_skill_path(scope: Scope) -> Path:
    if scope is Scope.user:
        return Path.home() / ".codex" / "skills" / "beakr"
    return Path.cwd() / ".agents" / "skills" / "beakr"


def _copy_tree(src: Path, dst: Path, force: bool) -> bool:
    if dst.exists() and not force:
        err_console.print(f"  [yellow]exists, skipped[/yellow] {dst} (use --force to overwrite)")
        return False
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst)
    return True


def _copy_glob(src: Path, dst: Path, pattern: str, force: bool) -> list[Path]:
    dst.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for f in sorted(src.glob(pattern)):
        target = dst / f.name
        if target.exists() and not force:
            err_console.print(f"  [yellow]exists, skipped[/yellow] {target}")
            continue
        shutil.copy2(f, target)
        written.append(target)
    return written


def _install_claude(scope: Scope, force: bool) -> None:
    skill_dst, cmds_dst = _claude_paths(scope)
    if _copy_tree(_skill_src(), skill_dst, force):
        console.print(f"[green]Claude:[/green] skill   → {skill_dst}")
    written = _copy_glob(_commands_src(), cmds_dst, "kb-*.md", force)
    for w in written:
        console.print(f"[green]Claude:[/green] command → {w}")


def _install_codex(scope: Scope, force: bool) -> None:
    skill_dst = _codex_skill_path(scope)
    if _copy_tree(_skill_src(), skill_dst, force):
        console.print(f"[green]Codex:[/green]  skill   → {skill_dst}")


def _uninstall_claude(scope: Scope) -> None:
    skill_dst, cmds_dst = _claude_paths(scope)
    if skill_dst.exists():
        shutil.rmtree(skill_dst)
        console.print(f"[red]Claude:[/red] removed {skill_dst}")
    if cmds_dst.exists():
        for f in sorted(cmds_dst.glob("kb-*.md")):
            f.unlink()
            console.print(f"[red]Claude:[/red] removed {f}")


def _uninstall_codex(scope: Scope) -> None:
    skill_dst = _codex_skill_path(scope)
    if skill_dst.exists():
        shutil.rmtree(skill_dst)
        console.print(f"[red]Codex:[/red]  removed {skill_dst}")


def install_command(
    client: Client,
    scope: Scope,
    uninstall: bool,
    force: bool,
) -> None:
    targets = _resolve_targets(client)
    if not targets:
        err_console.print(
            "No supported clients detected. Looked for [bold]~/.claude[/bold] and "
            "[bold]~/.codex[/bold]. Install one, or pass [bold]--client claude|codex|all[/bold]."
        )
        raise typer.Exit(1)

    for c in targets:
        if uninstall:
            if c == "claude":
                _uninstall_claude(scope)
            elif c == "codex":
                _uninstall_codex(scope)
        else:
            if c == "claude":
                _install_claude(scope, force)
            elif c == "codex":
                _install_codex(scope, force)

    if not uninstall:
        console.print(
            "\n[dim]Restart Claude Code / Codex if the skill doesn't appear immediately.[/dim]"
        )
