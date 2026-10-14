"""``beakr codex``: run Codex with fresh Beakr context, then capture the session.

Codex has no session hooks, so the spec's alternative (step 8.1 item 2) is a
pre-session wrapper. Each run:

1. refreshes the Beakr block in ``~/.codex/AGENTS.md`` with the context of the
   project bound to this directory (the block ``beakr hooks install --client
   codex`` writes), so Codex never starts from a stale snapshot;
2. runs ``codex`` with every argument passed through, attached to this
   terminal, and exits with its exit code;
3. afterwards, imports the Codex sessions written during the run, but only when
   this repository has opted in to capture (``beakr hooks capture on``).

A failed refresh or import is reported and logged, never turned into a failed
Codex run. After a failed refresh the block's capture notice is still rewritten
from local consent, and context left from another project is dropped; the
session can be imported later with ``beakr compound --from-codex-sessions``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import typer

from beakr_cli.hook_runtime import log_hook
from beakr_cli.output import err_console

#: Overrides the ``codex`` found on PATH (tests use a stand-in).
CODEX_BIN_ENV = "BEAKR_CODEX_BIN"

#: Sessions modified up to this long before the wrapper started are included,
#: so a file Codex created just before its first write is not missed.
_SESSION_SLACK_SECONDS = 60


def _codex_binary() -> str | None:
    return os.environ.get(CODEX_BIN_ENV) or shutil.which("codex")


def refresh_codex_context(cwd: Path) -> bool:
    """Rewrite the AGENTS.md Beakr block for the project bound at ``cwd``.

    The block always ends with a capture notice computed from this
    repository's local consent. When the engine cannot be reached the notice
    is still rewritten, and facts left from another directory or project are
    replaced, so Codex never starts with a notice or context that is wrong
    for where it runs.
    """
    from beakr_cli.codex_context import rewrite_from_local_state, write_codex_context
    from beakr_cli.compounding import project_for_session
    from beakr_cli.sdk import get_sdk

    try:
        with get_sdk() as sdk:
            result = sdk.engine.context(
                project_id=project_for_session(None, str(cwd)), format="agents_md"
            )
        write_codex_context(str(result.get("content") or ""), cwd)
        return True
    except Exception as exc:  # noqa: BLE001 - a refresh must never block Codex
        log_hook(f"codex wrapper: context refresh failed: {type(exc).__name__}: {exc}")
        err_console.print(
            f"[yellow]Beakr:[/yellow] could not refresh Codex context ({exc}); "
            "starting Codex with the last one for this project, if any."
        )
    try:
        rewrite_from_local_state(cwd, reason="the Beakr API could not be reached", create=True)
    except OSError as exc:
        log_hook(f"codex wrapper: could not rewrite the capture notice: {exc}")
    return False


def capture_codex_sessions(cwd: Path, started: float) -> int:
    """Import the Codex sessions written since ``started``, if this repository consents."""
    from beakr_cli.commands.install import _codex_home
    from beakr_cli.compounding import compound_codex_sessions
    from beakr_cli.project_binding import capture_enabled
    from beakr_cli.sdk import get_sdk

    if not capture_enabled(cwd):
        return 0
    since_hours = (time.time() - started + _SESSION_SLACK_SECONDS) / 3600
    try:
        with get_sdk() as sdk:
            outcomes = compound_codex_sessions(
                sdk, _codex_home(), since_hours=since_hours, require_consent=True
            )
    except Exception as exc:  # noqa: BLE001 - capture must never fail the Codex run
        log_hook(f"codex wrapper: session import failed: {type(exc).__name__}: {exc}")
        err_console.print(
            f"[yellow]Beakr:[/yellow] could not import this Codex session ({exc}); "
            "run `beakr compound --from-codex-sessions` later."
        )
        return 0
    return sum(1 for outcome in outcomes if outcome.imported)


def codex_command(ctx: typer.Context) -> None:
    """Run Codex with fresh Beakr context; capture the session if this project allows it.

    Every argument is passed to `codex` unchanged (`beakr codex exec "..."`).
    """
    binary = _codex_binary()
    if binary is None:
        err_console.print("Codex is not on PATH. Install Codex first.")
        raise typer.Exit(1)
    cwd = Path.cwd()
    refresh_codex_context(cwd)
    started = time.time()
    try:
        completed = subprocess.run([binary, *ctx.args], check=False)  # noqa: S603
    except KeyboardInterrupt:
        completed = subprocess.CompletedProcess([binary], 130)
    imported = capture_codex_sessions(cwd, started)
    if imported:
        err_console.print(f"[dim]Beakr: imported {imported} Codex session(s).[/dim]")
    raise typer.Exit(completed.returncode)


def register(root: typer.Typer) -> None:
    """Attach ``beakr codex``."""
    root.command(
        "codex",
        context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    )(codex_command)
