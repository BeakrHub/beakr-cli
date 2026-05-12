"""Install Beakr skills, slash commands, and MCP server into Claude Code / Codex."""

from __future__ import annotations

import importlib.resources
import json
import os
import shutil
import subprocess
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


def _claude_home() -> Path:
    """`~/.claude` by default; overridden by $CLAUDE_CONFIG_DIR."""
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def _codex_home() -> Path:
    """`~/.codex` by default; overridden by $CODEX_HOME."""
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def _claude_fallback_config() -> Path:
    return Path.home() / ".claude.json"


def _codex_config() -> Path:
    return _codex_home() / "config.toml"


def _assets_root() -> Path:
    return Path(str(importlib.resources.files("beakr_cli") / "assets"))


def _skill_src() -> Path:
    return _assets_root() / "skills" / "beakr"


def _commands_src() -> Path:
    return _assets_root() / "commands"


def _detect_clients() -> list[str]:
    found: list[str] = []
    if _claude_home().exists():
        found.append("claude")
    if _codex_home().exists():
        found.append("codex")
    return found


def _resolve_targets(client: Client) -> list[str]:
    if client is Client.auto:
        return _detect_clients()
    if client is Client.all:
        return ["claude", "codex"]
    return [client.value]


def _claude_paths(scope: Scope) -> tuple[Path, Path]:
    base = _claude_home() if scope is Scope.user else Path.cwd() / ".claude"
    return base / "skills" / "beakr", base / "commands"


def _codex_skill_path(scope: Scope) -> Path:
    if scope is Scope.user:
        return _codex_home() / "skills" / "beakr"
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


# ---------------------------------------------------------------------------
# MCP registration
# ---------------------------------------------------------------------------


def _beakr_command() -> str:
    """Resolve the absolute path to the `beakr` binary so MCP clients launched
    from GUI contexts (no shell PATH) can still find it."""
    return shutil.which("beakr") or "beakr"


def _claude_cli() -> str | None:
    return shutil.which("claude")


def _codex_cli() -> str | None:
    return shutil.which("codex")


def _register_claude_mcp(force: bool, scope: Scope) -> None:
    beakr = _beakr_command()
    claude = _claude_cli()
    cli_scope = scope.value  # "user" → ~/.claude.json; "project" → ./.mcp.json
    if claude:
        if force:
            subprocess.run(
                [claude, "mcp", "remove", "beakr", "--scope", cli_scope],
                capture_output=True,
                text=True,
            )
        result = subprocess.run(
            [claude, "mcp", "add", "beakr", "--scope", cli_scope, "--", beakr, "mcp"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            target = ".mcp.json" if scope is Scope.project else "~/.claude.json"
            console.print(f"[green]Claude:[/green] MCP registered (claude CLI → {target})")
            return
        stderr = (result.stderr or "").lower()
        if "already" in stderr or "exists" in stderr:
            err_console.print(
                "[yellow]Claude:[/yellow] MCP already registered (use --force to overwrite)"
            )
            return
        err_console.print(f"[red]Claude:[/red] claude mcp add failed: {result.stderr.strip()}")
        return

    # No claude CLI on PATH. Fallback paths depend on scope.
    if scope is Scope.project:
        _write_project_mcp_json(beakr, force)
        return

    # User scope, no CLI. If the user has a custom CLAUDE_CONFIG_DIR we don't
    # know where their MCP servers live, so don't guess — print the manual command.
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        err_console.print(
            "[yellow]Claude:[/yellow] no `claude` CLI on PATH and $CLAUDE_CONFIG_DIR is set; "
            f"register manually:\n  claude mcp add beakr -- {beakr} mcp"
        )
        return

    # Fallback: edit ~/.claude.json directly.
    config_path = _claude_fallback_config()
    data: dict = {}
    if config_path.exists():
        try:
            data = json.loads(config_path.read_text())
        except json.JSONDecodeError:
            err_console.print(
                f"[red]Claude:[/red] could not parse {config_path}; register manually:\n"
                f"  claude mcp add beakr -- {beakr} mcp"
            )
            return
    servers = data.setdefault("mcpServers", {})
    if "beakr" in servers and not force:
        err_console.print(
            f"[yellow]Claude:[/yellow] MCP already registered in {config_path} "
            "(use --force to overwrite)"
        )
        return
    servers["beakr"] = {"command": beakr, "args": ["mcp"]}
    config_path.write_text(json.dumps(data, indent=2) + "\n")
    console.print(f"[green]Claude:[/green] MCP registered ({config_path})")


def _write_project_mcp_json(beakr: str, force: bool) -> None:
    """Write `.mcp.json` in cwd — the documented project-scope MCP location for Claude Code."""
    path = Path.cwd() / ".mcp.json"
    data: dict = {}
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            err_console.print(
                f"[red]Claude:[/red] could not parse {path}; register manually:\n"
                f"  claude mcp add beakr --scope project -- {beakr} mcp"
            )
            return
    servers = data.setdefault("mcpServers", {})
    if "beakr" in servers and not force:
        err_console.print(
            f"[yellow]Claude:[/yellow] MCP already registered in {path} "
            "(use --force to overwrite)"
        )
        return
    servers["beakr"] = {"command": beakr, "args": ["mcp"]}
    path.write_text(json.dumps(data, indent=2) + "\n")
    console.print(f"[green]Claude:[/green] MCP registered ({path})")


def _unregister_claude_mcp(scope: Scope) -> None:
    claude = _claude_cli()
    cli_scope = scope.value
    removed = False
    if claude:
        result = subprocess.run(
            [claude, "mcp", "remove", "beakr", "--scope", cli_scope],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            console.print("[red]Claude:[/red] MCP removed (claude CLI)")
            removed = True

    # Fallback file depends on scope.
    fallback = Path.cwd() / ".mcp.json" if scope is Scope.project else _claude_fallback_config()
    if fallback.exists():
        try:
            data = json.loads(fallback.read_text())
        except json.JSONDecodeError:
            data = None
        if data is not None:
            servers = data.get("mcpServers") or {}
            if "beakr" in servers:
                del servers["beakr"]
                fallback.write_text(json.dumps(data, indent=2) + "\n")
                console.print(f"[red]Claude:[/red] MCP removed from {fallback}")
                removed = True
    if not removed:
        err_console.print("[yellow]Claude:[/yellow] no MCP registration found")


def _remove_toml_section(text: str, section: str) -> str:
    """Strip a `[section]` heading and its body up to the next heading or EOF."""
    target = f"[{section}]"
    out: list[str] = []
    skipping = False
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped == target:
            skipping = True
            continue
        if skipping and stripped.startswith("[") and stripped.endswith("]"):
            skipping = False
        if not skipping:
            out.append(line)
    return "".join(out)


def _codex_block() -> str:
    beakr = _beakr_command()
    return f'\n[mcp_servers.beakr]\ncommand = "{beakr}"\nargs = ["mcp"]\n'


def _register_codex_mcp(force: bool, scope: Scope) -> None:
    # Codex has no project-scope MCP — config.toml is global. Warn and proceed
    # at user scope so the user understands what actually happened.
    if scope is Scope.project:
        err_console.print(
            "[yellow]Codex:[/yellow]  no project-scope MCP support; "
            "registering at user scope (~/.codex/config.toml)"
        )

    beakr = _beakr_command()
    codex = _codex_cli()
    if codex:
        if force:
            subprocess.run(
                [codex, "mcp", "remove", "beakr"],
                capture_output=True,
                text=True,
            )
        result = subprocess.run(
            [codex, "mcp", "add", "beakr", "--", beakr, "mcp"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            console.print("[green]Codex:[/green]  MCP registered (codex CLI)")
            return
        stderr = (result.stderr or "").lower()
        if "already" in stderr or "exists" in stderr:
            err_console.print(
                "[yellow]Codex:[/yellow]  MCP already registered (use --force to overwrite)"
            )
            return
        err_console.print(
            f"[yellow]Codex:[/yellow]  codex mcp add failed ({result.stderr.strip()}); "
            "falling back to config.toml edit"
        )

    # Fallback: edit config.toml directly.
    config_path = _codex_config()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    block = _codex_block()
    if not config_path.exists():
        config_path.write_text(block.lstrip())
        console.print(f"[green]Codex:[/green]  MCP registered ({config_path})")
        return

    existing = config_path.read_text()
    if "[mcp_servers.beakr]" in existing:
        if not force:
            err_console.print(
                "[yellow]Codex:[/yellow]  MCP already registered (use --force to overwrite)"
            )
            return
        existing = _remove_toml_section(existing, "mcp_servers.beakr")

    if existing and not existing.endswith("\n"):
        existing += "\n"
    config_path.write_text(existing + block)
    console.print(f"[green]Codex:[/green]  MCP registered ({config_path})")


def _unregister_codex_mcp() -> None:
    codex = _codex_cli()
    removed = False
    if codex:
        result = subprocess.run(
            [codex, "mcp", "remove", "beakr"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            console.print("[red]Codex:[/red]  MCP removed (codex CLI)")
            removed = True

    config_path = _codex_config()
    if not config_path.exists():
        if not removed:
            err_console.print("[yellow]Codex:[/yellow]  no MCP registration found")
        return
    existing = config_path.read_text()
    if "[mcp_servers.beakr]" not in existing:
        if not removed:
            err_console.print("[yellow]Codex:[/yellow]  no MCP registration found")
        return
    new = _remove_toml_section(existing, "mcp_servers.beakr").rstrip()
    if new:
        config_path.write_text(new + "\n")
    else:
        config_path.unlink()
    console.print(f"[red]Codex:[/red]  MCP removed from {config_path}")


# ---------------------------------------------------------------------------
# Auth (lightweight inline prompt for the setup flow)
# ---------------------------------------------------------------------------


def _maybe_auth() -> None:
    """Prompt for an API key and verify it, unless one is already configured."""
    from beakr_cli import config as beakr_config
    from beakr_cli.client import api_get

    if os.environ.get("BEAKR_API_KEY") or beakr_config.get("api_key"):
        return

    console.print("\n[bold]Beakr authentication[/bold]")
    console.print("Get an API key at [cyan]https://thebeakr.com/settings/api-keys[/cyan]")
    api_key = typer.prompt("API key", hide_input=True)
    beakr_config.set_key("api_key", api_key)
    if not beakr_config.get("api_url"):
        beakr_config.set_key("api_url", "https://api.thebeakr.com")

    try:
        data = api_get("/v1/me")
    except Exception as exc:
        beakr_config.delete_key("api_key")
        err_console.print(f"[red]Auth failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    user = data.get("user", data)
    org = data.get("personal_org", data)
    console.print(
        f"Authenticated as [bold]{user.get('display_name', user.get('primary_email', 'unknown'))}[/bold] "
        f"in [bold]{org.get('name', org.get('slug', 'unknown'))}[/bold]"
    )


# ---------------------------------------------------------------------------
# Public commands
# ---------------------------------------------------------------------------


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


def setup_command(
    client: Client,
    scope: Scope,
    skip_auth: bool,
    skip_skills: bool,
    skip_mcp: bool,
    uninstall: bool,
    force: bool,
) -> None:
    """Auth + skills + MCP registration in one shot."""
    targets = _resolve_targets(client)
    if not targets:
        err_console.print(
            "No supported clients detected. Looked for [bold]~/.claude[/bold] and "
            "[bold]~/.codex[/bold]. Install Claude Code or Codex first, or pass "
            "[bold]--client claude|codex|all[/bold]."
        )
        raise typer.Exit(1)

    if uninstall:
        for c in targets:
            if c == "claude":
                _uninstall_claude(scope)
                if not skip_mcp:
                    _unregister_claude_mcp(scope)
            elif c == "codex":
                _uninstall_codex(scope)
                if not skip_mcp:
                    _unregister_codex_mcp()
        return

    if not skip_auth:
        _maybe_auth()

    if not skip_skills:
        for c in targets:
            if c == "claude":
                _install_claude(scope, force)
            elif c == "codex":
                _install_codex(scope, force)

    if not skip_mcp:
        for c in targets:
            if c == "claude":
                _register_claude_mcp(force, scope)
            elif c == "codex":
                _register_codex_mcp(force, scope)

    console.print(
        "\n[bold green]Done.[/bold green] Restart Claude Code / Codex to load the MCP server and skills."
    )
