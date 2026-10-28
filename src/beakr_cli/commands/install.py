"""Install Beakr skills, slash commands, and MCP server into Claude Code / Codex."""

from __future__ import annotations

import importlib.resources
import json
import os
import shlex
import shutil
import subprocess
import sys
from enum import Enum
from pathlib import Path

import typer
from rich.markup import escape

from beakr_cli.output import console, err_console
from beakr_cli.prompts import confirm_if_interactive


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
        err_console.print(
            f"  [yellow]exists, skipped[/yellow] {escape(str(dst))} (use --force to overwrite)"
        )
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
            err_console.print(f"  [yellow]exists, skipped[/yellow] {escape(str(target))}")
            continue
        shutil.copy2(f, target)
        written.append(target)
    return written


def _install_claude(scope: Scope, force: bool) -> None:
    skill_dst, cmds_dst = _claude_paths(scope)
    if _copy_tree(_skill_src(), skill_dst, force):
        console.print(f"[green]Claude:[/green] skill   → {escape(str(skill_dst))}")
    written = _copy_glob(_commands_src(), cmds_dst, "kb-*.md", force)
    for w in written:
        console.print(f"[green]Claude:[/green] command → {escape(str(w))}")


def _install_codex(scope: Scope, force: bool) -> None:
    skill_dst = _codex_skill_path(scope)
    if _copy_tree(_skill_src(), skill_dst, force):
        console.print(f"[green]Codex:[/green]  skill   → {escape(str(skill_dst))}")


def _uninstall_claude(scope: Scope) -> None:
    skill_dst, cmds_dst = _claude_paths(scope)
    if skill_dst.exists():
        shutil.rmtree(skill_dst)
        console.print(f"[red]Claude:[/red] removed {escape(str(skill_dst))}")
    if cmds_dst.exists():
        for f in sorted(cmds_dst.glob("kb-*.md")):
            f.unlink()
            console.print(f"[red]Claude:[/red] removed {escape(str(f))}")


def _uninstall_codex(scope: Scope) -> None:
    skill_dst = _codex_skill_path(scope)
    if skill_dst.exists():
        shutil.rmtree(skill_dst)
        console.print(f"[red]Codex:[/red]  removed {escape(str(skill_dst))}")


# ---------------------------------------------------------------------------
# MCP registration
# ---------------------------------------------------------------------------


def _beakr_command() -> str:
    """The absolute path of the `beakr` that is running, for MCP clients and hooks.

    Claude Code and Codex launched from a GUI have no shell PATH, so they need a
    path. It must be this install's: ``shutil.which("beakr")`` named whichever
    ``beakr`` came first on PATH, so on a machine with two installs (a
    self-hosted box client ahead of the ``uv tool`` one) setup wired Claude Code
    to the other install, which talks to another API. The running console
    script is ``sys.argv[0]``; under ``python -m beakr_cli`` it is the script
    installed beside the interpreter. PATH is the last resort.
    """
    for candidate in _running_entry_points():
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return shutil.which("beakr") or "beakr"


def _running_entry_points() -> list[Path]:
    """Where the running ``beakr`` console script can be, most specific first.

    Paths are made absolute but symlinks are kept: ``uv tool`` links
    ``~/.local/bin/beakr`` into a versioned environment, and the link is what
    keeps working after ``beakr update``.
    """
    script = "beakr.exe" if os.name == "nt" else "beakr"
    candidates: list[Path] = []
    argv0 = sys.argv[0] if sys.argv else ""
    if argv0 and Path(argv0).stem == "beakr":
        running = Path(os.path.abspath(argv0))
        candidates.append(running)
        if running.suffix == "" and os.name == "nt":
            candidates.append(running.with_suffix(".exe"))
    candidates.append(Path(sys.executable).parent / script)
    return candidates


def _mcp_env() -> dict[str, str]:
    """The settings an MCP server or hook needs to reach the API this CLI uses.

    A GUI-launched Claude Code or Codex passes no shell environment, so a
    sandbox or self-hosted ``BEAKR_BASE_URL`` (or saved ``api_url``) and a
    non-default ``BEAKR_HOME`` would otherwise be lost and the server would talk
    to production with no credential. Only non-default values are written. The
    API key never is: it stays in the token store under ``BEAKR_HOME``, which
    the server reads.
    """
    from beakr_sdk.credentials import (
        DEFAULT_BASE_URL,
        beakr_home,
        normalize_origin,
        resolve_base_url,
    )

    env: dict[str, str] = {}
    base_url = resolve_base_url()
    if normalize_origin(base_url) != normalize_origin(DEFAULT_BASE_URL):
        env["BEAKR_BASE_URL"] = base_url.rstrip("/")
    home = beakr_home()
    if os.path.abspath(home) != os.path.abspath(Path.home() / ".beakr"):
        env["BEAKR_HOME"] = os.path.abspath(home)
    return env


def _env_flags(flag: str, env: dict[str, str]) -> list[str]:
    """``[flag, KEY=value, ...]`` for ``claude mcp add -e`` / ``codex mcp add --env``."""
    flags: list[str] = []
    for key, value in env.items():
        flags += [flag, f"{key}={value}"]
    return flags


def _manual_claude_command(beakr: str, env: dict[str, str], *, project: bool = False) -> str:
    scope = " --scope project" if project else ""
    flags = "".join(f" -e {shlex.quote(f'{k}={v}')}" for k, v in env.items())
    return f"claude mcp add beakr{scope}{flags} -- {shlex.quote(beakr)} mcp"


def _mcp_server_entry(beakr: str, env: dict[str, str]) -> dict[str, object]:
    entry: dict[str, object] = {"command": beakr, "args": ["mcp"]}
    if env:
        entry["env"] = env
    return entry


def _claude_cli() -> str | None:
    return shutil.which("claude")


def _codex_cli() -> str | None:
    return shutil.which("codex")


def _register_claude_mcp(force: bool, scope: Scope) -> None:
    beakr = _beakr_command()
    env = _mcp_env()
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
            [
                claude,
                "mcp",
                "add",
                "beakr",
                "--scope",
                cli_scope,
                *_env_flags("-e", env),
                "--",
                beakr,
                "mcp",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            target = ".mcp.json" if scope is Scope.project else "~/.claude.json"
            console.print(
                f"[green]Claude:[/green] MCP registered (claude CLI → {escape(str(target))})"
            )
            return
        stderr = (result.stderr or "").lower()
        if "already" in stderr or "exists" in stderr:
            err_console.print(
                "[yellow]Claude:[/yellow] MCP already registered (use --force to overwrite)"
            )
            return
        err_console.print(
            f"[red]Claude:[/red] claude mcp add failed: {escape(str(result.stderr.strip()))}"
        )
        return

    # No claude CLI on PATH. Fallback paths depend on scope.
    if scope is Scope.project:
        _write_project_mcp_json(beakr, env, force)
        return

    # User scope, no CLI. If the user has a custom CLAUDE_CONFIG_DIR we don't
    # know where their MCP servers live, so don't guess — print the manual command.
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        err_console.print(
            "[yellow]Claude:[/yellow] no `claude` CLI on PATH and $CLAUDE_CONFIG_DIR is set; "
            f"register manually:\n  {escape(_manual_claude_command(beakr, env))}"
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
                f"[red]Claude:[/red] could not parse {escape(str(config_path))}; register "
                "manually:\n"
                f"  {escape(_manual_claude_command(beakr, env))}"
            )
            return
    servers = data.setdefault("mcpServers", {})
    if "beakr" in servers and not force:
        err_console.print(
            f"[yellow]Claude:[/yellow] MCP already registered in {escape(str(config_path))} "
            "(use --force to overwrite)"
        )
        return
    servers["beakr"] = _mcp_server_entry(beakr, env)
    config_path.write_text(json.dumps(data, indent=2) + "\n")
    console.print(f"[green]Claude:[/green] MCP registered ({escape(str(config_path))})")


def _write_project_mcp_json(beakr: str, env: dict[str, str], force: bool) -> None:
    """Write `.mcp.json` in cwd — the documented project-scope MCP location for Claude Code."""
    path = Path.cwd() / ".mcp.json"
    data: dict = {}
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            err_console.print(
                f"[red]Claude:[/red] could not parse {escape(str(path))}; register manually:\n"
                f"  {escape(_manual_claude_command(beakr, env, project=True))}"
            )
            return
    servers = data.setdefault("mcpServers", {})
    if "beakr" in servers and not force:
        err_console.print(
            f"[yellow]Claude:[/yellow] MCP already registered in {escape(str(path))} (use --force "
            "to overwrite)"
        )
        return
    servers["beakr"] = _mcp_server_entry(beakr, env)
    path.write_text(json.dumps(data, indent=2) + "\n")
    console.print(f"[green]Claude:[/green] MCP registered ({escape(str(path))})")


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
                console.print(f"[red]Claude:[/red] MCP removed from {escape(str(fallback))}")
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


def _toml_string(value: str) -> str:
    """A TOML basic string; JSON's escaping is valid TOML for these values."""
    return json.dumps(value)


def _codex_block() -> str:
    beakr = _beakr_command()
    block = f'\n[mcp_servers.beakr]\ncommand = {_toml_string(beakr)}\nargs = ["mcp"]\n'
    env = _mcp_env()
    if env:
        pairs = ", ".join(f"{key} = {_toml_string(value)}" for key, value in env.items())
        block += f"env = {{ {pairs} }}\n"
    return block


def _register_codex_mcp(force: bool, scope: Scope) -> None:
    # Codex has no project-scope MCP — config.toml is global. Warn and proceed
    # at user scope so the user understands what actually happened.
    if scope is Scope.project:
        err_console.print(
            "[yellow]Codex:[/yellow]  no project-scope MCP support; "
            "registering at user scope (~/.codex/config.toml)"
        )

    beakr = _beakr_command()
    env = _mcp_env()
    codex = _codex_cli()
    if codex:
        if force:
            subprocess.run(
                [codex, "mcp", "remove", "beakr"],
                capture_output=True,
                text=True,
            )
        result = subprocess.run(
            [codex, "mcp", "add", "beakr", *_env_flags("--env", env), "--", beakr, "mcp"],
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
            "[yellow]Codex:[/yellow]  codex mcp add failed "
            f"({escape(str(result.stderr.strip()))}); "
            "falling back to config.toml edit"
        )

    # Fallback: edit config.toml directly.
    config_path = _codex_config()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    block = _codex_block()
    if not config_path.exists():
        config_path.write_text(block.lstrip())
        console.print(f"[green]Codex:[/green]  MCP registered ({escape(str(config_path))})")
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
    console.print(f"[green]Codex:[/green]  MCP registered ({escape(str(config_path))})")


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
    console.print(f"[red]Codex:[/red]  MCP removed from {escape(str(config_path))}")


# ---------------------------------------------------------------------------
# Auth (lightweight inline prompt for the setup flow)
# ---------------------------------------------------------------------------


def _identity_summary(data: dict) -> str:
    user = data.get("user", data)
    org = data.get("personal_org", data)
    name = user.get("display_name") or user.get("primary_email") or "unknown"
    org_name = org.get("name") or org.get("slug") or "unknown"
    return f"{name} in {org_name}"


def _sign_in_new() -> None:
    """Sign in through the browser, the default since 0.4.0.

    Pasting a key is still possible with ``beakr auth login --with-api-key`` or
    ``$BEAKR_API_KEY``; setup no longer asks for one because a browser login
    works for SSO-only orgs and needs no key to be created first.
    """
    from beakr_sdk.credentials import resolve_base_url

    from beakr_cli.commands.auth import _login_with_oauth

    console.print("\n[bold]Beakr sign-in[/bold]")
    console.print("[dim]To use an API key instead, run: beakr auth login --with-api-key[/dim]")
    _login_with_oauth(resolve_base_url(), open_browser=sys.stdin.isatty(), device=False)


def _verified_identity() -> str | None:
    """Who the current credential signs in as, or None when it does not verify."""
    from beakr_cli.sdk import get_sdk

    try:
        with get_sdk() as sdk:
            return _identity_summary(sdk.me.get())
    except (Exception, SystemExit):
        return None


def _save_env_key(env_key: str) -> None:
    """Save ``$BEAKR_API_KEY`` the way ``beakr auth login --with-api-key`` does.

    The MCP server and hooks setup registers are started by Claude Code or
    Codex, often from a GUI without this shell's environment, so a key that
    lived only in the environment reached none of them: setup reported auth as
    done while the server started signed out. The key goes to the token store
    (keychain, else a 0600 file under ``BEAKR_HOME``), filed under the API it
    verified against, never into the MCP registration itself.

    The API it verified against is saved as ``api_url`` too, as ``beakr auth
    login --with-api-key`` saves it. A key filed under a sandbox or self-hosted
    origin given only by ``$BEAKR_BASE_URL`` was otherwise unreachable from
    anything started without that variable (a plugin's MCP server, a new
    shell, an SDK script): they resolved production and found "No Beakr
    credentials for https://api.thebeakr.com".
    """
    from beakr_sdk.credentials import TokenStore, resolve_base_url

    from beakr_cli import config as beakr_config

    base_url = resolve_base_url()
    beakr_config.set_key("api_url", base_url)
    beakr_config.save_api_key(env_key, base_url)
    where = "the keychain" if TokenStore().backend == "keyring" else "the Beakr credentials file"
    console.print(
        f"Saved [bold]$BEAKR_API_KEY[/bold] to {where} for {escape(base_url)} so Claude Code "
        "and Codex can use it."
    )


def _maybe_auth() -> None:
    """Keep a working sign-in (env key, browser login, saved key) or create one."""
    from beakr_sdk.credentials import TokenStore

    from beakr_cli import config as beakr_config

    env_key = os.environ.get("BEAKR_API_KEY")
    # A browser sign-in or a saved API key: both live in the token store (an
    # older CLI's key in config.json is moved there on read).
    beakr_config.migrate_secrets()
    stored = TokenStore().load()

    # 1. Env var wins -- verify and announce, don't prompt.
    if env_key:
        identity = _verified_identity()
        if identity is None:
            err_console.print("[red]$BEAKR_API_KEY didn't verify.[/red]")
            raise typer.Exit(3)
        console.print(f"Using [bold]$BEAKR_API_KEY[/bold] ([dim]{escape(str(identity))}[/dim])")
        if stored is None:
            _save_env_key(env_key)
        return

    # 2. A browser login or saved key -- verify, then ask whether to keep it.
    if stored is not None:
        label = "API key" if stored.is_api_key else "browser sign-in"
        identity = _verified_identity()
        if identity is None:
            err_console.print(
                f"[yellow]Existing {escape(str(label))} didn't verify; let's sign in "
                "again.[/yellow]"
            )
            TokenStore().clear()
            _sign_in_new()
            return

        # Non-interactive (piped/CI): keep the existing verified credential silently.
        if not sys.stdin.isatty():
            console.print(
                f"Using existing {escape(str(label))} ([dim]{escape(str(identity))}[/dim])"
            )
            return

        console.print(
            f"\nExisting {escape(str(label))} found for [bold]{escape(str(identity))}[/bold]."
        )
        if confirm_if_interactive("Keep using it?", default=True):
            return
        TokenStore().clear()
        _sign_in_new()
        return

    # 3. Nothing configured -- sign in.
    _sign_in_new()


# ---------------------------------------------------------------------------
# Public commands
# ---------------------------------------------------------------------------


def refresh_installed_assets() -> int:
    """Overwrite skills and slash commands wherever a previous install put them.

    Run by ``beakr update`` through the freshly upgraded binary, so the copies in
    ~/.claude, ~/.codex and the current project match the tools this version's
    MCP server actually exposes. It never installs into a location that did not
    already have the skill: an upgrade should not widen where Beakr is wired in.
    Returns the number of locations refreshed.
    """
    refreshed = 0
    for scope in (Scope.user, Scope.project):
        claude_skill, _ = _claude_paths(scope)
        if claude_skill.exists():
            _install_claude(scope, force=True)
            refreshed += 1
        if _codex_skill_path(scope).exists():
            _install_codex(scope, force=True)
            refreshed += 1
    if refreshed == 0:
        console.print("[dim]No installed Beakr skills found to refresh.[/dim]")
    return refreshed


def install_command(
    client: Client,
    scope: Scope,
    uninstall: bool,
    force: bool,
) -> dict[str, object]:
    """Install (or remove) the skills and slash commands; return what was done."""
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
    return {
        "action": "uninstall" if uninstall else "install",
        "clients": targets,
        "scope": scope.value,
    }


def setup_command(
    client: Client,
    scope: Scope,
    skip_auth: bool,
    skip_skills: bool,
    skip_mcp: bool,
    uninstall: bool,
    force: bool,
    hooks: bool | None = None,
) -> dict[str, object]:
    """Auth + skills + MCP registration + session hooks in one shot; return what was done.

    ``hooks`` is the ``--hooks/--no-hooks`` flag: True installs, False skips,
    None asks when interactive and skips otherwise.
    """
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
        # Setup installs the session hooks at user level whatever the scope,
        # so uninstalling at either scope removes them too: an uninstall must
        # never leave automatic capture running (audit C074). Only Beakr's own
        # hooks and AGENTS.md block go; anything else is left as it was.
        from beakr_cli.commands.hooks import uninstall_hooks_during_setup

        uninstall_hooks_during_setup(targets)
        return {
            "action": "uninstall",
            "clients": targets,
            "scope": scope.value,
            "skills": True,
            "mcp": not skip_mcp,
            "hooks": True,
        }

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

    from beakr_cli.commands.hooks import install_hooks_during_setup

    install_hooks_during_setup(targets, hooks)

    console.print("\n[bold green]Done.[/bold green]")
    console.print("Restart Claude Code / Codex to load the MCP server and skills.")
    return {
        "action": "install",
        "clients": targets,
        "scope": scope.value,
        "auth": not skip_auth,
        "skills": not skip_skills,
        "mcp": not skip_mcp,
        # The --hooks/--no-hooks flag as given; None means "asked when interactive".
        "hooks": hooks,
    }
