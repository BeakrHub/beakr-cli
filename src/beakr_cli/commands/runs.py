"""``beakr ask`` and ``beakr runs ls|show|cancel``: Beakr's agent from the terminal.

``ask`` starts an agent run and streams the answer as it is written, then lists
the sources it cited. Runs live in threads: ``runs ls`` lists your recent
threads with each one's latest run id, ``runs show RUN_ID`` reads one run's
state and ``runs cancel RUN_ID`` stops it.
"""

from __future__ import annotations

import sys
from enum import Enum
from pathlib import Path
from typing import Any

import typer

from beakr_cli.cited_answer import number_citations, print_cited_sources
from beakr_cli.output import console, emit, err_console, page_rows, print_table, short
from beakr_cli.project_ref import project_ref_callback
from beakr_cli.sdk import get_sdk

app = typer.Typer(help="Agent runs: list recent threads, show a run, cancel one.")

_json_opt = typer.Option(False, "--json", "-j", help="Output raw JSON.")
_FAILED = {"error", "failed", "cancelled"}

#: A citation key is short; text held back waiting for a closing ``}}`` longer
#: than this is ordinary text and is released rather than stalling the stream.
_MAX_PENDING = 256


class AgentMode(str, Enum):
    """The research tiers ``POST /v1/runs`` accepts (the engine's ``StartRunRequest.mode``).

    Offering only these means a mistyped or retired name ("fast") is a usage
    error here instead of a 422 from the server after the files are uploaded.
    """

    free = "free"
    quick = "quick"
    deep = "deep"
    extreme = "extreme"
    custom = "custom"
    auto = "auto"
    auto_free = "auto_free"


class _CitationStream:
    """Numbers citations in streamed text even when a token spans deltas (C063).

    Everything that cannot be the start of an unfinished ``{{key}}`` token is
    released immediately; only the shortest suffix that still could be one
    (a trailing ``{``, or an unclosed ``{{...``) is held for the next delta.
    """

    def __init__(self, order: list[str]) -> None:
        self.order = order
        self.pending = ""

    def feed(self, delta: str) -> str:
        text = number_citations(self.pending + delta, self.order)
        cut = len(text)
        opening = text.rfind("{{")
        if opening != -1 and "}}" not in text[opening:]:
            cut = opening
        elif text.endswith("{"):
            cut = len(text) - 1
        if len(text) - cut > _MAX_PENDING:
            cut = len(text)
        self.pending = text[cut:]
        return text[:cut]

    def finish(self) -> str:
        rest, self.pending = self.pending, ""
        return rest


def ask(
    prompt: str = typer.Argument(..., help="What to ask Beakr's agent."),
    project: str | None = typer.Option(
        None,
        "--project",
        "-P",
        "--scope",
        help="Project to work in (and scope Knowledge to).",
        callback=project_ref_callback,
    ),
    file: list[Path] = typer.Option(  # noqa: B008 - typer reads the default
        [], "--file", "-f", help="A file to upload and give the agent; repeatable."
    ),
    thread: str | None = typer.Option(
        None, "--thread", "-t", help="Continue a thread instead of starting one."
    ),
    mode: AgentMode | None = typer.Option(
        None, "--mode", help="Research tier, e.g. quick or deep. Default: your seat's default."
    ),
    knowledge: bool = typer.Option(
        True,
        "--knowledge/--no-knowledge",
        help="Let the agent read Knowledge while it answers (on by default).",
    ),
    json_out: bool = typer.Option(
        False, "--json", "-j", help="Print the run's events as JSON lines."
    ),
) -> None:
    """Ask Beakr's agent and stream the answer, with the sources it cited."""
    for path in file:
        if not path.is_file():
            raise typer.BadParameter(f"Not a file: {path}", param_hint="--file")
    with get_sdk(timeout=300.0) as sdk:
        # Each file is uploaded, then handed to the run by id (the run's
        # ``files``), so the agent reads it as it would a file attached in chat.
        file_ids = [
            str(sdk.files.upload(path, project_id=project)["artifact"]["id"]) for path in file
        ]
        run = sdk.runs.start(
            prompt,
            project_id=project,
            thread_id=thread,
            mode=mode.value if mode else None,
            files=file_ids or None,
            knowledge=knowledge,
        )
        run_id = str(run.get("run_id") or run.get("id"))
        if not json_out:
            err_console.print(
                f"[dim]Run {run_id} in thread {run.get('thread_id')}[/dim]", soft_wrap=True
            )
        sources: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        citations = _CitationStream(order)
        text: list[str] = []
        status = "completed"
        errored = False
        for event in sdk.runs.stream(run_id):
            kind = event.get("type")
            data = event.get("data") or {}
            # Outcome is tracked whatever the output format, so `--json` exits
            # nonzero on a failed run exactly as text mode does (C062).
            if kind == "error":
                errored = True
            elif kind == "stream_complete":
                status = str(data.get("status") or status)
            if json_out:
                import json

                print(json.dumps(event), flush=True)
                continue
            if kind == "content_delta" and not data.get("is_thinking"):
                chunk = citations.feed(str(data.get("delta") or ""))
                if chunk:
                    text.append(chunk)
                    sys.stdout.write(chunk)
                    sys.stdout.flush()
            elif kind == "sources":
                for source in data.get("sources") or []:
                    if source.get("key"):
                        sources[str(source["key"])] = source
            elif kind == "error":
                err_console.print(f"[red]{data.get('message') or data}[/red]")
    if json_out:
        if errored or status in _FAILED:
            raise typer.Exit(1)
        return
    rest = citations.finish()
    if rest:
        text.append(rest)
        sys.stdout.write(rest)
    if text and not text[-1].endswith("\n"):
        sys.stdout.write("\n")
    print_cited_sources(order, sources)
    if status in _FAILED:
        err_console.print(f"[red]Run {run_id} ended {status}.[/red]")
    if errored or status in _FAILED:
        raise typer.Exit(1)


@app.command("ls")
def list_runs(
    limit: int = typer.Option(20, "--limit", "-n", min=1, max=100, help="How many threads."),
    query: str | None = typer.Option(None, "--search", "-q", help="Filter threads by text."),
    json_out: bool = _json_opt,
) -> None:
    """Your recent agent threads, newest first, with each thread's latest run id
    (pass it to `beakr runs show` or `beakr runs cancel`)."""
    with get_sdk() as sdk:
        listing = sdk.threads.list(q=query, limit=limit)

    def render(data: dict[str, Any]) -> None:
        threads = page_rows(data)
        if not threads:
            err_console.print('No threads yet. Start one with `beakr ask "..."`.')
            return
        print_table(
            ["Thread ID", "Latest run", "Run status", "Title", "Last activity"],
            [
                [
                    str(t.get("thread_id") or ""),
                    str(t.get("latest_run_id") or "-"),
                    str(t.get("latest_run_status") or ""),
                    short(t.get("title") or t.get("last_message_preview"), 40),
                    str(t.get("latest_message_at") or t.get("updated_at") or "")[:16],
                ]
                for t in threads
            ],
            title=f"Threads ({len(threads)})",
        )

    emit(listing, json_out=json_out, render=render)


@app.command("show")
def show_run(
    run_id: str = typer.Argument(..., help="Run id from `beakr ask` or the stream."),
    json_out: bool = _json_opt,
) -> None:
    """A run's status, thread and timing."""
    with get_sdk() as sdk:
        run = sdk.runs.get(run_id)

    def render(data: dict[str, Any]) -> None:
        for label, key in (
            ("Run", "run_id"),
            ("Thread", "thread_id"),
            ("Status", "status"),
            ("Started", "started_at"),
            ("Finished", "completed_at"),
            ("Error", "error"),
        ):
            value = data.get(key) or (data.get("id") if key == "run_id" else None)
            if value:
                console.print(f"{label + ':':<10}{value}", highlight=False)
        answer = _answer_lines(data)
        if answer:
            console.print()
            console.print("\n".join(answer), markup=False, highlight=False)

    def lines(data: dict[str, Any]) -> list[str]:
        from beakr_cli.compact import summary_line

        answer = _answer_lines(data)
        return [summary_line(data), *(["", *answer] if answer else [])]

    emit(run, json_out=json_out, render=render, lines=lines)


def _answer_lines(run: dict[str, Any]) -> list[str]:
    """A completed run's answer (citations as [n]) and its numbered sources.

    ``runs show`` printed the run's state only, so reading an answer later
    meant re-running the question; the run carries it once it is completed.
    A paused run shows the question it is waiting on instead.
    """
    from beakr_sdk.sources import source_where

    answer = run.get("answer")
    if isinstance(answer, dict) and isinstance(answer.get("text"), str):
        out = [answer["text"].strip()]
        sources = [s for s in answer.get("sources") or [] if isinstance(s, dict)]
        if sources:
            out += ["", "Sources:"]
            out += [f"  [{n}] {source_where(s)}" for n, s in enumerate(sources, 1)]
        return out
    question = run.get("question")
    if isinstance(question, dict) and question.get("text"):
        return [f"Waiting on a question: {question['text']}"]
    return []


@app.command("cancel")
def cancel_run(
    run_id: str = typer.Argument(..., help="Run id to stop."),
    json_out: bool = _json_opt,
) -> None:
    """Stop a running agent run."""
    with get_sdk() as sdk:
        result = sdk.runs.cancel(run_id)
    emit(
        result if isinstance(result, dict) else {"run_id": run_id, "cancel_requested": True},
        json_out=json_out,
        render=lambda _: console.print(f"Asked run {run_id} to stop."),
    )


def register(root: typer.Typer) -> None:
    root.command("ask")(ask)
    root.add_typer(app, name="runs")
