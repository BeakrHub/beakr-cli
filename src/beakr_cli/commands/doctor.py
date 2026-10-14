"""``beakr doctor``: check that the CLI, credentials, MCP and hooks are wired up."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

import httpx
import typer

from beakr_cli.output import console, emit

Status = Literal["ok", "warn", "fail"]


def _check(name: str, status: Status, detail: str, **extra: Any) -> dict[str, Any]:
    return {"name": name, "status": status, "detail": detail, **extra}


def check_version() -> dict[str, Any]:
    from beakr_cli import __version__
    from beakr_cli.updates import get_update_status

    try:
        status = get_update_status(allow_network=False)
    except Exception:
        return _check("version", "ok", f"beakr-cli {__version__}", version=__version__)
    if status.update_available:
        return _check(
            "version",
            "warn",
            f"beakr-cli {__version__}; {status.latest} is available (beakr update)",
            version=__version__,
            latest=status.latest,
        )
    return _check("version", "ok", f"beakr-cli {__version__}", version=__version__)


def check_reachable(base_url: str) -> dict[str, Any]:
    try:
        response = httpx.get(f"{base_url}/v1/public/openapi.json", timeout=5.0)
    except httpx.HTTPError as exc:
        return _check("api", "fail", f"{base_url} unreachable: {exc}", base_url=base_url)
    # Any HTTP answer means the host is up; the route may not exist on older servers.
    return _check("api", "ok", f"{base_url} (HTTP {response.status_code})", base_url=base_url)


def _check_clerk_token_scopes(sdk: Any) -> dict[str, Any]:
    """A Clerk-issued token holds only the Beakr scopes it was granted (none by default).

    Probe one ordinary read: an ``insufficient_scope`` answer means the token
    was issued without Beakr scopes, which a fresh `beakr auth login` (it asks
    for them) fixes unless the Clerk OAuth app does not offer them.
    """
    from beakr_sdk.errors import BeakrError, PermissionDeniedError

    try:
        sdk.projects.list()
    except PermissionDeniedError as exc:
        if exc.code == "insufficient_scope":
            return _check(
                "scopes",
                "fail",
                f"Clerk sign-in lacks Beakr scope '{exc.required_scope or 'unknown'}'. "
                "Run `beakr auth login` again (it requests the Beakr scopes); if this "
                "persists, the Clerk OAuth application must allow Beakr's scopes.",
                issuer="clerk",
            )
        return _check("scopes", "warn", f"unknown ({exc})", issuer="clerk")
    except BeakrError as exc:
        return _check("scopes", "warn", f"unknown ({exc})", issuer="clerk")
    return _check("scopes", "ok", "Clerk sign-in: the Beakr scopes it was granted", issuer="clerk")


def check_auth_and_scopes(sdk: Any) -> list[dict[str, Any]]:
    from beakr_sdk.errors import BeakrError

    try:
        credential = sdk.ensure_fresh_credential()
    except BeakrError as exc:
        return [_check("auth", "fail", str(exc)), _check("scopes", "warn", "unknown")]
    try:
        me = sdk.me.get()
    except BeakrError as exc:
        return [
            _check("auth", "fail", f"{credential.source}: {exc}", source=credential.source),
            _check("scopes", "warn", "unknown"),
        ]
    user = me.get("user", {}) if isinstance(me, dict) else {}
    who = user.get("display_name") or user.get("primary_email") or "unknown"
    auth = _check("auth", "ok", f"{who} via {credential.source}", source=credential.source)

    if credential.kind == "access_token":
        tokens = credential.tokens
        if tokens is not None and tokens.issuer == "clerk":
            return [auth, _check_clerk_token_scopes(sdk)]
        return [auth, _check("scopes", "ok", "browser login: your role's permissions")]
    if credential.kind == "dev_identity":
        return [auth, _check("scopes", "ok", "local dev identity")]
    try:
        keys = sdk.keys.list()
    except BeakrError:
        return [auth, _check("scopes", "warn", "unknown (cannot list keys with this credential)")]
    token = credential.token or ""
    match = next(
        (k for k in keys if k.get("key_prefix") and token.startswith(str(k["key_prefix"]))),
        None,
    )
    if match is None:
        return [auth, _check("scopes", "warn", "unknown")]
    scopes = match.get("scopes")
    if scopes is None:
        return [auth, _check("scopes", "ok", "legacy full access", scopes=None)]
    return [auth, _check("scopes", "ok", ", ".join(scopes) or "none", scopes=scopes)]


def check_binding() -> dict[str, Any]:
    from beakr_cli.project_binding import find_binding, read_binding

    path = find_binding()
    binding = read_binding()
    if not binding:
        return _check("project", "warn", "this directory is not bound (beakr init)")
    return _check(
        "project",
        "ok",
        f"{binding.get('name')} ({binding.get('project_id')})",
        path=str(path),
    )


def _json_has_beakr_server(path: Path) -> bool:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and "beakr" in (data.get("mcpServers") or {})


def check_mcp() -> dict[str, Any]:
    from beakr_cli.commands.install import _claude_fallback_config, _codex_config

    found = []
    if _json_has_beakr_server(_claude_fallback_config()) or _json_has_beakr_server(
        Path.cwd() / ".mcp.json"
    ):
        found.append("claude")
    try:
        if "[mcp_servers.beakr]" in _codex_config().read_text():
            found.append("codex")
    except OSError:
        pass
    if not found:
        return _check("mcp", "warn", "not registered (beakr setup)", clients=[])
    return _check("mcp", "ok", "registered for " + ", ".join(found), clients=found)


def check_hooks() -> dict[str, Any]:
    try:
        from beakr_cli.commands.hooks import hooks_status
    except (ImportError, AttributeError):
        return _check("hooks", "warn", "unknown")
    try:
        status = hooks_status()
    except Exception as exc:
        return _check("hooks", "warn", f"unknown ({exc})")
    installed = [
        name
        for name, value in (status or {}).items()
        if isinstance(value, dict) and value.get("installed")
    ]
    if not installed:
        return _check("hooks", "warn", "not installed (beakr hooks install)", hooks=status)
    return _check("hooks", "ok", "installed for " + ", ".join(installed), hooks=status)


def check_hook_queue() -> dict[str, Any]:
    """Session imports that failed in a hook and wait for retry (``~/.beakr/hook_queue``)."""
    from beakr_cli.hook_queue import MAX_QUEUED, queue_health

    health = queue_health()
    if health.queued == 0:
        return _check("queue", "ok", "no pending session imports", **health.as_dict())
    age = health.oldest_age_seconds or 0.0
    detail = f"{health.queued}/{MAX_QUEUED} session imports waiting, oldest {age / 3600:.1f}h"
    if health.healthy:
        return _check("queue", "ok", detail + " (retried on the next hook run)", **health.as_dict())
    hint = " -- full: newer turns wait in the transcript" if health.full else ""
    return _check("queue", "warn", f"{detail}{hint}; see ~/.beakr/hooks.log", **health.as_dict())


def run_checks() -> list[dict[str, Any]]:
    from beakr_cli.sdk import get_sdk

    checks = [check_version()]
    with get_sdk(timeout=10.0) as sdk:
        checks.append(check_reachable(sdk.base_url))
        checks.extend(check_auth_and_scopes(sdk))
    for check in (check_binding, check_mcp, check_hooks, check_hook_queue):
        checks.append(_safe(check))
    return checks


def _safe(check: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return check()
    except Exception as exc:
        return _check(check.__name__.removeprefix("check_"), "warn", f"check failed: {exc}")


_STYLE = {"ok": "green", "warn": "yellow", "fail": "red"}


def doctor_command(
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
) -> None:
    """Check version, API reachability, credentials, scopes, binding, MCP, hooks and queue."""
    checks = run_checks()
    ok = not any(c["status"] == "fail" for c in checks)

    def render(rows: list[dict[str, Any]]) -> None:
        for c in rows:
            style = _STYLE[c["status"]]
            console.print(f"[{style}]{c['status']:<4}[/{style}]  {c['name']:<8} {c['detail']}")

    emit({"ok": ok, "checks": checks}, json_out=json_out, render=lambda d: render(d["checks"]))
    if not ok:
        raise typer.Exit(1)


def register(root: typer.Typer) -> None:
    root.command("doctor")(doctor_command)
