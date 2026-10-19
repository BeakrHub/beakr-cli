"""``beakr context`` and ``beakr compound``: what agents read at the start and send at the end.

Both run as agent hooks. In ``--hook`` mode a command never fails the agent's
session: errors go to ``~/.beakr/hooks.log`` and the exit code is 0.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import typer
from beakr_sdk.resources.conversations import CONVERSATION_CLIENTS
from beakr_sdk.resources.engine import CONTEXT_FORMATS

from beakr_cli.compounding import (
    DEFAULT_MIN_INTERVAL_SECONDS,
    compound_codex_sessions,
    import_and_compound,
)
from beakr_cli.hook_runtime import hooks_log_path, log_hook, read_hook_stdin
from beakr_cli.output import console, err_console, print_json
from beakr_cli.project_binding import AUTO, capture_enabled, resolve_project
from beakr_cli.project_ref import project_ref_callback
from beakr_cli.sdk import get_sdk
from beakr_cli.transcripts import parse_transcript

#: ``X-Beakr-Client`` for hook traffic, so the API can tell automated capture
#: from a person running the same command.
HOOK_CLIENT_NAME = "claude-code-hook"

#: Shown to the agent with its context, so the session knows whether capture
#: is on. Capture needs the repository's own consent (spec step 8.1 item 3).
CAPTURE_NOTICE = (
    "Beakr session capture is on for this project: new messages from this session are "
    "imported into your private Beakr conversations as it runs (turn off with "
    "`beakr hooks capture off`)."
)
CAPTURE_OFF_NOTICE = (
    "Beakr session capture is off for this project: this session is not imported. "
    "To capture sessions here, run `beakr hooks capture on` in a bound repository."
)

#: Set in the environment of every process a hook starts. A hook that finds it
#: set is running inside another hook's work (for example an agent session
#: launched while capturing) and does nothing, so capture can never recurse.
HOOK_ACTIVE_ENV = "BEAKR_HOOK_ACTIVE"


def _inside_another_hook() -> bool:
    return bool(os.environ.get(HOOK_ACTIVE_ENV))


def _client_name(hook: bool) -> str | None:
    return HOOK_CLIENT_NAME if hook else None


# ---------------------------------------------------------------------------
# context
# ---------------------------------------------------------------------------


def context(
    project: str | None = typer.Option(
        AUTO,
        "--project",
        "-P",
        "--scope",
        help="Project id, slug or name, or 'auto' for the bound/default project.",
        callback=project_ref_callback,
    ),
    question: str | None = typer.Option(None, "--question", "-q", help="Focus the context."),
    since: str | None = typer.Option(None, "--since", help="Include changes since (ISO time)."),
    fmt: str = typer.Option(
        "markdown", "--format", "-f", help="claude_md, agents_md, markdown or json."
    ),
    budget_chars: int | None = typer.Option(None, "--budget-chars", help="Size budget."),
    out: Path | None = typer.Option(None, "--out", "-o", help="Write the content to a file."),
    hook: bool = typer.Option(
        False, "--hook", help="Claude Code SessionStart mode: JSON output, never fails."
    ),
    json_out: bool = typer.Option(False, "--json", "-j", help="Print the full JSON response."),
) -> None:
    """Print onboarding context (profiles, memories, changes, skills) for an agent session."""
    if fmt not in CONTEXT_FORMATS:
        err_console.print(
            f"Invalid --format '{fmt}'. Choose: {', '.join(CONTEXT_FORMATS)}.", markup=False
        )
        raise typer.Exit(2)
    if hook:
        _context_hook(project, question, since, fmt, budget_chars)
        return
    with get_sdk() as sdk:
        result = sdk.engine.context(
            project_id=resolve_project(project),
            question=question,
            since=since,
            format=fmt,
            budget_chars=budget_chars,
        )
    content = str(result.get("content") or "")
    if out is not None:
        if fmt == "json":
            # The file holds what stdout would print for this format: the whole
            # response as JSON, not its rendered markdown.
            content = json.dumps(result, indent=2, default=str, ensure_ascii=False) + "\n"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(content)
        err_console.print(f"Wrote {len(content)} characters to {out}", markup=False)
        if json_out:
            print_json(result)
        return
    if json_out or fmt == "json":
        print_json(result)
        return
    sys.stdout.write(content if content.endswith("\n") else content + "\n")


def _context_hook(
    project: str | None,
    question: str | None,
    since: str | None,
    fmt: str,
    budget_chars: int | None,
) -> None:
    """SessionStart: emit ``hookSpecificOutput.additionalContext`` or nothing at all."""
    if _inside_another_hook():
        return
    payload = read_hook_stdin()
    try:
        # Claude Code runs the hook in the session's directory, but it also
        # says which one: prefer that for the project binding lookup.
        project_id = resolve_project(project)
        if (project in (None, AUTO)) and payload.get("cwd"):
            from beakr_cli.compounding import project_for_session

            project_id = project_for_session(None, str(payload["cwd"]))
        with get_sdk(client_name=HOOK_CLIENT_NAME) as sdk:
            result = sdk.engine.context(
                project_id=project_id,
                question=question,
                since=since,
                format=fmt,
                budget_chars=budget_chars,
            )
        content = str(result.get("content") or "").rstrip()
    except BaseException as exc:  # noqa: BLE001 - a hook must never break the session
        if isinstance(exc, KeyboardInterrupt):
            raise
        log_hook(f"context hook failed: {type(exc).__name__}: {exc}")
        return
    notice = (
        CAPTURE_NOTICE if capture_enabled(payload.get("cwd") or os.getcwd()) else CAPTURE_OFF_NOTICE
    )
    additional = f"{content}\n\n{notice}" if content else notice
    print_json(
        {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": additional,
            }
        }
    )


# ---------------------------------------------------------------------------
# compound
# ---------------------------------------------------------------------------


def compound(
    conversation_id: str | None = typer.Argument(
        None, help="An imported conversation to compound."
    ),
    from_transcript: Path | None = typer.Option(
        None, "--from-transcript", help="Import this session file first."
    ),
    client: str = typer.Option(
        "claude-code", "--client", "-c", help="claude-code or codex (for --from-transcript)."
    ),
    from_codex_sessions: bool = typer.Option(
        False, "--from-codex-sessions", help="Import new or changed Codex sessions."
    ),
    since_hours: float | None = typer.Option(
        None, "--since-hours", help="With --from-codex-sessions: only files this recent."
    ),
    project: str | None = typer.Option(
        None,
        "--project",
        "-P",
        "--scope",
        help="Project id, slug or name (default: the session's bound project).",
        callback=project_ref_callback,
    ),
    background: bool = typer.Option(False, "--background", help="Detach and return immediately."),
    hook: bool = typer.Option(
        False, "--hook", help="Agent hook mode: read hook JSON on stdin, never fail."
    ),
    force: bool = typer.Option(False, "--force", help="Compound even if done recently."),
    min_interval: int = typer.Option(
        DEFAULT_MIN_INTERVAL_SECONDS,
        "--min-interval",
        help="Seconds before the same session is compounded again.",
    ),
    json_out: bool = typer.Option(False, "--json", "-j", help="Output JSON."),
    detached_child: bool = typer.Option(False, "--detached-child", hidden=True),
    session_cwd: str | None = typer.Option(None, "--session-cwd", hidden=True),
) -> None:
    """Import a session's new messages and queue the conversation for compounding.

    Imported conversations are private to you. Re-running on a growing
    transcript sends only the messages added since the last run.
    """
    if client not in CONVERSATION_CLIENTS:
        choices = ", ".join(CONVERSATION_CLIENTS)
        err_console.print(f"Invalid --client '{client}'. Choose: {choices}.", markup=False)
        raise typer.Exit(2)
    # The detached child carries the marker its parent hook set; any other hook
    # process that finds it set is nested inside a hook and must stop.
    if hook and not detached_child and _inside_another_hook():
        return
    if hook and from_transcript is None:
        payload = read_hook_stdin()
        transcript = payload.get("transcript_path")
        if isinstance(transcript, str) and transcript:
            from_transcript = Path(transcript)
        if session_cwd is None and isinstance(payload.get("cwd"), str):
            session_cwd = payload["cwd"]
    selected = sum(bool(v) for v in (conversation_id, from_transcript, from_codex_sessions))
    if selected != 1:
        if hook:
            log_hook("compound hook: no transcript_path on stdin; nothing to do")
            return
        err_console.print(
            "Provide exactly one of CONVERSATION_ID, --from-transcript or --from-codex-sessions."
        )
        raise typer.Exit(2)

    if background:
        _spawn_detached(
            _child_args(
                conversation_id,
                from_transcript,
                client,
                from_codex_sessions,
                since_hours,
                project,
                hook,
                force,
                min_interval,
                session_cwd,
            )
        )
        return

    try:
        result = _run_compound(
            conversation_id,
            from_transcript,
            client,
            from_codex_sessions,
            since_hours,
            project,
            hook,
            force,
            min_interval,
            session_cwd,
        )
    except BaseException as exc:
        if not hook or isinstance(exc, (KeyboardInterrupt, typer.Exit)):
            raise
        log_hook(f"compound hook failed: {type(exc).__name__}: {exc}")
        return
    if hook:
        log_hook(f"compound: {result}")
        return
    _print_result(result, json_out)


def _run_compound(
    conversation_id: str | None,
    from_transcript: Path | None,
    client: str,
    from_codex_sessions: bool,
    since_hours: float | None,
    project: str | None,
    hook: bool,
    force: bool,
    min_interval: int,
    session_cwd: str | None = None,
) -> Any:
    with get_sdk(client_name=_client_name(hook)) as sdk:
        if conversation_id:
            return sdk.engine.compound(conversation_id)
        if from_transcript is not None:
            if not from_transcript.is_file():
                raise FileNotFoundError(f"Transcript not found: {from_transcript}")
            session = parse_transcript(from_transcript, client)
            if session.cwd is None and session_cwd:
                # The hook says where the session ran when the file does not.
                session.cwd = session_cwd
            return import_and_compound(
                sdk,
                session,
                client=client,
                project=project,
                force=force,
                min_interval=min_interval,
                # A hook cannot wait out an outage: queue and retry next turn.
                queue_on_failure=hook,
                # Automatic capture needs the repository's consent; a person
                # running this command deliberately does not.
                require_consent=hook,
            ).as_dict()
        from beakr_cli.commands.install import _codex_home

        outcomes = compound_codex_sessions(
            sdk, _codex_home(), project=project, since_hours=since_hours, require_consent=hook
        )
        return [outcome.as_dict() for outcome in outcomes]


def _print_result(result: Any, json_out: bool) -> None:
    if json_out:
        print_json(result)
        return
    rows = result if isinstance(result, list) else [result]
    if not rows:
        console.print("No new sessions to import.")
        return
    for row in rows:
        if "session_id" not in row:
            if row.get("status") == "nothing_to_compound":
                console.print(
                    f"Nothing to compound in {row.get('conversation_id')}: no unread "
                    "message text is held for it.",
                    markup=False,
                )
            else:
                pending = row.get("pending_messages")
                suffix = f" ({pending} message(s) pending)" if pending else ""
                console.print(
                    f"Compounding {row.get('status', 'queued')} for "
                    f"{row.get('conversation_id')}{suffix}.",
                    markup=False,
                )
            continue
        state = "compounding queued" if row["compounded"] else f"not compounded ({row['reason']})"
        if row.get("new_messages") is not None:
            state = f"{row['new_messages']} new message(s), {state}"
        console.print(
            f"{row['session_id']}: conversation {row['conversation_id']}, {state}", markup=False
        )


def _child_args(
    conversation_id: str | None,
    from_transcript: Path | None,
    client: str,
    from_codex_sessions: bool,
    since_hours: float | None,
    project: str | None,
    hook: bool,
    force: bool,
    min_interval: int,
    session_cwd: str | None = None,
) -> list[str]:
    """The same compound, minus --background, with the stdin-derived path made explicit."""
    args = ["compound", "--client", client, "--min-interval", str(min_interval)]
    if conversation_id:
        args.append(conversation_id)
    if from_transcript is not None:
        args += ["--from-transcript", str(from_transcript.expanduser().resolve())]
    if from_codex_sessions:
        args.append("--from-codex-sessions")
    if since_hours is not None:
        args += ["--since-hours", str(since_hours)]
    if project:
        args += ["--project", project]
    if hook:
        args += ["--hook", "--detached-child"]
    if session_cwd:
        args += ["--session-cwd", session_cwd]
    if force:
        args.append("--force")
    return args


def _spawn_detached(args: list[str]) -> None:
    """Re-run ``beakr <args>`` detached so the agent is not kept waiting.

    The child is started through the interpreter running this CLI rather than
    a ``beakr`` looked up on PATH, so a hook always runs the version that
    installed it.
    """
    log_path = hooks_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        subprocess.Popen(
            [sys.executable, "-m", "beakr_cli.main", *args],
            env={**os.environ, HOOK_ACTIVE_ENV: "1"},
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
            close_fds=True,
        )


def register(root: typer.Typer) -> None:
    """Attach ``beakr context``, ``beakr compound`` and ``beakr conversations``."""
    from beakr_cli.commands.conversations import app as conversations_app

    root.command("context")(context)
    root.command("compound")(compound)
    root.add_typer(conversations_app, name="conversations")
