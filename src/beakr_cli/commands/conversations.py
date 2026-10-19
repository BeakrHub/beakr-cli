"""``beakr conversations``: sessions imported from Claude Code, Codex and others."""

from __future__ import annotations

import json
from itertools import islice
from pathlib import Path
from typing import Any

import typer
from beakr_sdk.resources.conversations import CONVERSATION_CLIENTS

from beakr_cli.chatgpt_export import ChatGPTExportError, parse_chatgpt_export
from beakr_cli.compounding import import_and_compound
from beakr_cli.output import console, emit, err_console, print_table, short
from beakr_cli.project_binding import resolve_project
from beakr_cli.project_ref import project_ref_callback
from beakr_cli.sdk import get_sdk
from beakr_cli.transcripts import HEADLESS, client_summary, parse_transcript

app = typer.Typer(help="List, read, import and delete imported agent conversations.")

#: What each ``compound_status`` means for the person reading it. ``none`` is
#: the engine's state for a conversation never sent to the compounder (its
#: ``imported_conversations.compound_status`` check allows none, queued,
#: running, done, partial, skipped and failed); ``pending`` stands in when a
#: row carries no status. ``partial`` and ``skipped`` keep the unprocessed
#: messages, so a later compound retries them.
_COMPOUND_STATES = {
    "none": "not compounded yet",
    "pending": "not compounded yet",
    "queued": "queued",
    "running": "running",
    "done": "done",
    "partial": "partial (the rest is kept and retried on the next compound)",
    "skipped": "skipped (too little to learn from yet; kept for later)",
    "failed": "failed (kept; run `beakr compound` to retry)",
}


def compound_state(row: dict[str, Any]) -> str:
    """``compound_status`` in words, with the reason from ``last_compound_result``."""
    status = str(row.get("compound_status") or "pending")
    text = _COMPOUND_STATES.get(status, status)
    result = row.get("last_compound_result")
    if isinstance(result, dict):
        reason = result.get("reason") or result.get("error")
        if reason and status not in {"done", "queued", "running"}:
            text = f"{text}: {reason}"
    return text


def _compound_cell(row: dict[str, Any]) -> str:
    # A Beakr chat is captured into Knowledge by Beakr itself, never compounded.
    if row.get("kind") == "ask_thread":
        return "-"
    through = row.get("compounded_through")
    total = row.get("message_count")
    status = str(row.get("compound_status") or "pending")
    label = _COMPOUND_CELL_LABELS.get(status, status)
    if isinstance(through, int) and isinstance(total, int):
        return f"{label} {through}/{total}"
    return label


#: The table cell's short form of the states whose raw value reads as nothing.
_COMPOUND_CELL_LABELS = {"none": "not yet", "pending": "not yet"}


_json_opt = typer.Option(False, "--json", "-j", help="Output JSON.")


@app.command("ls")
def list_conversations(
    client: str | None = typer.Option(None, "--client", "-c", help="Filter by client."),
    project: str | None = typer.Option(
        None,
        "--project",
        "-P",
        "--scope",
        help="Filter by project id.",
        callback=project_ref_callback,
    ),
    query: str | None = typer.Option(None, "--query", "-q", help="Search titles and summaries."),
    limit: int = typer.Option(50, "--limit", "-n", help="Max conversations."),
    json_out: bool = _json_opt,
) -> None:
    """List your imported conversations and Beakr chats, newest first."""
    with get_sdk() as sdk:
        items = list(
            islice(
                sdk.conversations.iter(
                    client=client, project_id=project, q=query, page_size=min(limit, 200)
                ),
                limit,
            )
        )

    def render(rows: list[dict[str, Any]]) -> None:
        if not rows:
            err_console.print("No conversations.")
            return
        print_table(
            ["ID", "Client", "Title", "Messages", "Compound", "Updated"],
            [
                [
                    str(row.get("id", "")),
                    short(row.get("client"), 12),
                    short(row.get("title"), 40),
                    str(row.get("message_count", "")),
                    _compound_cell(row),
                    short(row.get("updated_at"), 16),
                ]
                for row in rows
            ],
        )

    emit(items, json_out=json_out, render=render)


@app.command("show")
def show_conversation(
    conversation_id: str = typer.Argument(..., help="Conversation id."),
    json_out: bool = _json_opt,
) -> None:
    """Show one conversation, with its messages when stored as full text."""
    with get_sdk() as sdk:
        item = sdk.conversations.get(conversation_id)

    def render(data: dict[str, Any]) -> None:
        console.print(f"[bold]{data.get('title') or data.get('id')}[/bold]")
        console.print(
            f"Client: {data.get('client')}  Session: {data.get('session_id')}  "
            f"Messages: {data.get('message_count')}"
        )
        console.print(
            f"Compounded: {data.get('compounded_through', 0)}/{data.get('message_count')} "
            f"messages, {compound_state(data)}"
        )
        if data.get("summary"):
            console.print(f"\n{data['summary']}")
        for message in data.get("messages") or []:
            console.print(f"\n[bold]{message.get('role')}[/bold]: {message.get('content')}")

    def lines(data: dict[str, Any]) -> list[str]:
        # Piped, the summary line alone dropped the summary and the messages.
        from beakr_cli.compact import summary_line

        out = [summary_line(data)]
        if data.get("summary"):
            out += ["", str(data["summary"])]
        for message in data.get("messages") or []:
            out += ["", f"{message.get('role')}: {message.get('content')}"]
        return out

    emit(item, json_out=json_out, render=render, lines=lines)


@app.command("search")
def search_conversations(
    query: str = typer.Argument(..., help="What it was about; a question works with --any."),
    project: str | None = typer.Option(
        None,
        "--project",
        "-P",
        "--scope",
        help="Project id, slug or name.",
        callback=project_ref_callback,
    ),
    any_words: bool = typer.Option(
        False, "--any", help="Match any word of a question and rank by how many match."
    ),
    no_chats: bool = typer.Option(False, "--no-chats", help="Only imported sessions."),
    limit: int = typer.Option(10, "--limit", "-n", min=1, max=25),
    json_out: bool = _json_opt,
) -> None:
    """Search your conversations: imported sessions and your Beakr chats."""
    with get_sdk() as sdk:
        result = sdk.conversations.search(
            query,
            project_id=resolve_project(project) if project else None,
            include_threads=not no_chats,
            match="any" if any_words else "all",
            limit=limit,
        )
    hits = result.get("data", []) if isinstance(result, dict) else []

    def render(rows: list[dict[str, Any]]) -> None:
        if not rows:
            console.print("No matching conversations.")
            return
        for hit in rows:
            kind = "imported session" if hit.get("kind") == "imported" else "Beakr chat"
            day = str(hit.get("updated_at") or "")[:10]
            console.print(f"[bold]{hit.get('title') or '(untitled)'}[/bold]  {kind}, {day}")
            console.print(f"  id: {hit.get('id')}")
            if hit.get("summary"):
                console.print(f"  {short(str(hit['summary']), 300)}")
            findings = hit.get("findings") or []
            if findings:
                titles = [f.get("page_title") for f in findings if f.get("page_title")]
                console.print(f"  findings: {len(findings)}  " + ", ".join(titles[:5]))

    emit(hits, json_out=json_out, render=render)


@app.command("freshness")
def conversation_freshness(
    conversation_id: str = typer.Argument(..., help="Imported conversation id."),
    json_out: bool = _json_opt,
) -> None:
    """Are the pages an imported session read unchanged since it read them?"""
    with get_sdk() as sdk:
        result = sdk.conversations.freshness(conversation_id)

    def render(data: dict[str, Any]) -> None:
        if not data.get("indexed"):
            console.print("Not indexed yet: run `beakr compound` on it first.")
            return
        coverage = data.get("coverage") or "none"
        checked = data.get("pages_checked", 0)
        fresh = data.get("fresh")
        if fresh is True:
            console.print(f"Fresh: all {checked} pages it read are unchanged (full coverage).")
        elif fresh is False:
            console.print("[yellow]Changed since this session read them:[/yellow]")
            for page in data.get("changed") or []:
                console.print(
                    f"  - {page.get('title')} ({page.get('state')}, read revision "
                    f"{page.get('read_revision')}, now {page.get('current_revision')})"
                )
        elif coverage == "none":
            console.print(
                "Unknown: nothing it read could be checked (no page revisions were "
                "reported for this session)."
            )
        else:
            console.print(
                f"Unknown: {checked} checked pages are unchanged, but some reads "
                "could not be checked (partial coverage)."
            )
        pages = data.get("unchecked_pages") or []
        if pages:
            titles = ", ".join(str(p.get("title") or p.get("page_id")) for p in pages[:10])
            console.print(f"Not checked (revision unknown): {titles}")
        sources = data.get("unchecked_sources") or []
        if sources:
            refs = ", ".join(str(s.get("source_ref")) for s in sources[:10])
            console.print(f"Not checked (by source reference): {refs}")
        files = data.get("unchecked_files") or []
        if files:
            console.print(f"Not checked (outside Beakr): {', '.join(files[:10])}")

    emit(result, json_out=json_out, render=render)


@app.command("import")
def import_conversation(
    from_transcript: Path | None = typer.Option(
        None,
        "--from-transcript",
        "--from",
        help=(
            "A Claude Code or Codex session file, or with --client chatgpt-export a ChatGPT "
            "data export (the zip or its conversations.json; every conversation in it)."
        ),
    ),
    file: Path | None = typer.Option(
        None, "--file", help="JSON with session_id, messages and optional title/summary."
    ),
    client: str = typer.Option("claude-code", "--client", "-c", help="Which agent produced it."),
    project: str | None = typer.Option(
        None,
        "--project",
        "-P",
        "--scope",
        help="Project id, slug or name.",
        callback=project_ref_callback,
    ),
    store_full_text: bool | None = typer.Option(
        None,
        "--full-text/--no-full-text",
        help=(
            "Store the messages, or only a summary and references. Default: your full-text "
            "setting for this client."
        ),
        show_default=False,
    ),
    summary: str | None = typer.Option(
        None, "--summary", help="What the session was about; kept instead of the transcript."
    ),
    compound: bool = typer.Option(
        False,
        "--compound",
        help="Have Beakr read the session into Knowledge now (its text is held until then).",
    ),
    interactive: bool = typer.Option(
        False,
        "--interactive",
        help=(
            "Mark the session as one you typed yourself, so personal learning may "
            "read it. Off by default; refused for a transcript its agent recorded "
            "as headless (claude -p, the Agent SDK, codex exec, a subagent)."
        ),
    ),
    json_out: bool = _json_opt,
) -> None:
    """Import a session. Imported conversations are private to you.

    A transcript is imported incrementally: running this again sends only the
    messages added since, positioned after the stored ones (the cursor is
    shared with the session hooks). A ``--file`` is sent as given: its
    messages are the whole session unless it sets ``base_index``.
    """
    if client not in CONVERSATION_CLIENTS:
        choices = ", ".join(CONVERSATION_CLIENTS)
        err_console.print(f"Invalid --client '{client}'. Choose: {choices}.")
        raise typer.Exit(2)
    if bool(from_transcript) == bool(file):
        err_console.print("Provide exactly one of --from-transcript or --file.")
        raise typer.Exit(2)
    if from_transcript is not None and client == "chatgpt-export":
        _import_chatgpt_export(
            from_transcript,
            project=project,
            store_full_text=store_full_text,
            summary=summary,
            compound=compound,
            interactive=interactive,
            json_out=json_out,
        )
        return
    if from_transcript is not None:
        try:
            session = parse_transcript(from_transcript, client)
        except ValueError as exc:
            err_console.print(str(exc))
            raise typer.Exit(2) from exc
        if interactive and session.classification == HEADLESS:
            err_console.print(
                "--interactive refused: this transcript was recorded by a headless run "
                "(claude -p, the Agent SDK, codex exec or a subagent)."
            )
            raise typer.Exit(2)
        # By default Beakr keeps a summary and references, never the transcript,
        # and refuses an import that would keep nothing (`summary_required`).
        # Without --summary, send a deterministic client-side one.
        if not summary and store_full_text is not True:
            # Also when full text may come from the person's setting: the
            # summary is what is kept if it does not.
            summary = client_summary(session)
        with get_sdk() as sdk:
            outcome = import_and_compound(
                sdk,
                session,
                client=client,
                project=project,
                compound=compound,
                force=compound,
                store_full_text=store_full_text,
                summary=summary,
                interactive=interactive,
            )
        emit(
            outcome.as_dict(),
            json_out=json_out,
            render=lambda r: console.print(
                f"Sent {r['new_messages']} new message(s) to conversation "
                f"{r['conversation_id'] or '(none yet)'}."
            ),
        )
        return
    else:
        assert file is not None
        data = json.loads(file.read_text())
        if not isinstance(data, dict) or not data.get("session_id"):
            err_console.print("--file must be a JSON object with session_id and messages.")
            raise typer.Exit(2)
        # A --file is sent as given: without "base_index" its messages are the
        # whole session (a resend keeps identical leading messages); with it,
        # they start at that position.
        base_index = data.get("base_index")
        if base_index is not None and (not isinstance(base_index, int) or base_index < 0):
            err_console.print("--file base_index must be a non-negative integer.")
            raise typer.Exit(2)
        body = {
            "session_id": str(data["session_id"]),
            "base_index": base_index,
            "messages": data.get("messages"),
            "files_touched": data.get("files_touched"),
            "sources_read": data.get("sources_read"),
            "tools_used": data.get("tools_used"),
            "title": data.get("title"),
            "summary": data.get("summary"),
            "project_id": resolve_project(project),
        }
    with get_sdk() as sdk:
        result = sdk.conversations.import_(
            client=client,
            interactive=interactive,
            store_full_text=store_full_text,
            **body,
        )
    emit(
        result,
        json_out=json_out,
        render=lambda r: console.print(
            f"{'Imported' if r.get('created') else 'Updated'} conversation "
            f"{r.get('conversation_id')} (stored: {r.get('stored')})."
        ),
    )


def _import_chatgpt_export(
    path: Path,
    *,
    project: str | None,
    store_full_text: bool | None,
    summary: str | None,
    compound: bool,
    interactive: bool,
    json_out: bool,
) -> None:
    """Import every conversation in a ChatGPT data export, each as its own session."""
    if summary:
        err_console.print("--summary names one session; an export holds many. Leave it out.")
        raise typer.Exit(2)
    try:
        sessions = parse_chatgpt_export(path)
    except ChatGPTExportError as exc:
        err_console.print(str(exc))
        raise typer.Exit(2) from exc
    if not sessions:
        err_console.print(f"No conversations with messages in {path}.")
        raise typer.Exit(1)
    results: list[dict[str, Any]] = []
    with get_sdk() as sdk:
        for session in sessions:
            outcome = import_and_compound(
                sdk,
                session,
                client="chatgpt-export",
                project=project,
                compound=compound,
                force=compound,
                store_full_text=store_full_text,
                summary=None if store_full_text is True else client_summary(session),
                interactive=interactive,
            )
            results.append({"title": session.title, **outcome.as_dict()})

    def render(rows: list[dict[str, Any]]) -> None:
        sent = sum(int(r.get("new_messages") or 0) for r in rows)
        console.print(
            f"Imported {len(rows)} ChatGPT conversation(s), {sent} new message(s) in all."
        )

    emit(results, json_out=json_out, render=render)


@app.command("full-text")
def full_text_setting(
    clients: list[str] = typer.Argument(
        None,
        help=(
            "Clients whose imports keep full text by default, e.g. `claude-code codex`. "
            "Omit to show the setting."
        ),
    ),
    clear: bool = typer.Option(
        False, "--none", help="Keep only summaries and references for every client."
    ),
    json_out: bool = _json_opt,
) -> None:
    """Show or set which clients' imports keep the full transcript (per person).

    Every other client's imports keep a summary and references only (task,
    entities, files, tools and what the session read). An import's own
    --full-text/--no-full-text overrides this either way.
    """
    if clear and clients:
        err_console.print("Pass clients or --none, not both.")
        raise typer.Exit(2)
    unknown = [c for c in clients or [] if c not in CONVERSATION_CLIENTS]
    if unknown:
        err_console.print(
            f"Unknown client(s): {', '.join(unknown)}. Choose: {', '.join(CONVERSATION_CLIENTS)}."
        )
        raise typer.Exit(2)
    with get_sdk() as sdk:
        if clients or clear:
            result = sdk.conversations.update_settings(full_text_clients=list(clients or []))
        else:
            result = sdk.conversations.get_settings()

    def render(data: dict[str, Any]) -> None:
        chosen = data.get("full_text_clients") or []
        if chosen:
            console.print(f"Full text is kept for imports from: {', '.join(chosen)}.")
        else:
            console.print("Full text is kept for no client: imports keep a summary and references.")

    emit(result, json_out=json_out, render=render)


@app.command("rm")
def remove_conversation(
    conversation_id: str = typer.Argument(..., help="Conversation id."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
    json_out: bool = _json_opt,
) -> None:
    """Delete an imported conversation."""
    from beakr_cli.prompts import confirm_or_require_yes

    confirm_or_require_yes(f"Delete conversation {conversation_id}?", yes)
    with get_sdk() as sdk:
        sdk.conversations.delete(conversation_id)
    emit(
        {"id": conversation_id, "deleted": True},
        json_out=json_out,
        render=lambda _: console.print(f"Deleted conversation {conversation_id}."),
    )
