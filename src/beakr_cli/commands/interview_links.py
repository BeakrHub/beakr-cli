"""``beakr interviews link create|ls|show|send|revoke``: send a link, read what they said.

One call creates the interview and its link; hand the link to the person however
you like (Beakr sends nothing). ``show`` prints what they answered and ``send``
puts a finished interview into one or more projects' Knowledge.
"""

from __future__ import annotations

from typing import Any

import typer
from rich.markup import escape

from beakr_cli.output import console, emit, err_console, print_markdown, print_table, short
from beakr_cli.project_ref import project_ref_callback
from beakr_cli.sdk import get_sdk

app = typer.Typer(help="Interview links: create one, read its result, send it to projects.")

_json_opt = typer.Option(False, "--json", "-j", help="Output raw JSON.")


@app.command("create")
def create(
    title: str = typer.Argument(..., help="What the interview is about."),
    goal: str | None = typer.Option(None, "--goal", "-g", help="What you want to learn."),
    question: list[str] = typer.Option(  # noqa: B008
        [], "--question", "-q", help="A key question to cover; repeatable."
    ),
    name: str | None = typer.Option(None, "--name", help="Who it is for (a label only)."),
    email: str | None = typer.Option(
        None, "--email", help="Their email, shown beside the link; Beakr sends nothing."
    ),
    expires_at: str | None = typer.Option(
        None, "--expires-at", help="ISO 8601 with a time zone; defaults to 14 days."
    ),
    json_out: bool = _json_opt,
) -> None:
    """Create an interview and one link to it; prints the link (shown once)."""
    if not goal and not question:
        raise typer.BadParameter("give --goal, --question, or both", param_hint="--goal")
    with get_sdk() as sdk:
        link = sdk.interviews.create_link(
            title,
            goal=goal,
            questions=question or None,
            recipient_name=name,
            recipient_email=email,
            expires_at=expires_at,
        )

    def render(data: dict[str, Any]) -> None:
        console.print(str(data.get("url") or ""), highlight=False, markup=False)
        err_console.print(
            f"Send this link to the person; it is shown only once. Link id {data.get('link_id')}"
            f" (expires {data.get('expires_at')}). Read the result with "
            f"`beakr interviews link show {data.get('link_id')}`.",
            highlight=False,
            markup=False,
        )

    emit(link, json_out=json_out, render=render)


def _link_id(link: dict[str, Any]) -> str:
    return str(link.get("link_id") or link.get("id") or "")


def _resolve_link_id(sdk: Any, ref: str) -> str:
    """The link id for ``ref``: a link id as given, or an interview's only link.

    ``ls`` lists interviews and each interview has its own links; accepting an
    interview id here means the id people copy from ``ls`` works whenever the
    interview has exactly one link. Anything else is passed through unchanged
    so the API's own not-found answer reaches the user.
    """
    for interview in sdk.interviews.sendables():
        links = interview.get("links") or []
        if any(_link_id(link) == ref for link in links):
            return ref
        if str(interview.get("id")) == ref:
            if len(links) == 1:
                return _link_id(links[0])
            ids = ", ".join(_link_id(link) for link in links) or "none"
            err_console.print(
                f"Interview {ref} has {len(links)} links; pass one of their link ids: {ids}. "
                "`beakr interviews link ls` lists them.",
                markup=False,
            )
            raise typer.Exit(2)
    return ref


def _ls_lines(data: list[dict[str, Any]]) -> list[str]:
    """One line per link (agents and scripts read these): link id, interview, for, status."""
    out = []
    for interview in data:
        for link in interview.get("links") or [{}]:
            out.append(
                "\t".join(
                    [
                        _link_id(link) or "-",
                        str(interview.get("title") or ""),
                        f"for={link.get('label') or '-'}",
                        f"status={link.get('status') or '-'}",
                    ]
                )
            )
    return out


@app.command("ls")
def list_links(json_out: bool = _json_opt) -> None:
    """Your interviews and their links, newest first (show, send and revoke take the link id)."""
    with get_sdk() as sdk:
        rows = sdk.interviews.sendables()

    def render(data: list[dict[str, Any]]) -> None:
        if not data:
            err_console.print("No interviews yet. Create one with `beakr interviews link create`.")
            return
        table_rows = []
        for interview in data:
            links = interview.get("links") or [{}]
            for link in links:
                table_rows.append(
                    [
                        _link_id(link) or "-",
                        short(interview.get("title"), 36),
                        short(link.get("label"), 20) if link else "-",
                        str(link.get("status") or "-"),
                        str(link.get("created_at") or interview.get("created_at") or "")[:16],
                    ]
                )
        print_table(
            ["Link ID", "Interview", "For", "Status", "Created"],
            table_rows,
            title=f"Interviews ({len(data)})",
        )
        err_console.print(
            "Read one with `beakr interviews link show <link id>`; file it into Knowledge "
            "with `beakr interviews link send <link id> -P <project>`."
        )

    emit(
        rows,
        json_out=json_out,
        render=render,
        lines=_ls_lines,
        hint="beakr interviews link show <link id>",
    )


def _write_up_text(data: dict[str, Any]) -> str:
    note = data.get("write_up")
    if isinstance(note, dict):
        note = note.get("text")
    return note if isinstance(note, str) else ""


def _show_lines(data: dict[str, Any]) -> list[str]:
    out = [f"status={data.get('status')}"]
    write_up = _write_up_text(data)
    if write_up:
        out += ["write-up:", write_up]
    for turn in data.get("transcript") or []:
        who = "interviewer" if turn.get("type") == "agent_question" else "answer"
        out.append(f"{who}: {turn.get('text') or ''}")
    for item in data.get("knowledge") or []:
        out.append(
            f"knowledge: {item.get('project_name') or item.get('project_id')} {item.get('status')}"
        )
    return out


@app.command("show")
def show(
    link_id: str = typer.Argument(
        ..., help="A link id from `create` or `ls` (or an interview id with one link)."
    ),
    json_out: bool = _json_opt,
) -> None:
    """What the person answered: status, the write-up and the transcript."""
    with get_sdk() as sdk:
        result = sdk.interviews.link_result(_resolve_link_id(sdk, link_id))

    def render(data: dict[str, Any]) -> None:
        console.print(f"Status: {data.get('status')}", highlight=False, markup=False)
        if not data.get("completed_at"):
            err_console.print("Not finished yet.")
        write_up = _write_up_text(data)
        if write_up:
            console.rule("Write-up")
            print_markdown(write_up)
        turns = data.get("transcript") or []
        if turns:
            console.rule("Transcript")
            for turn in turns:
                who = "Interviewer" if turn.get("type") == "agent_question" else "Answer"
                console.print(f"[bold]{escape(str(who))}:[/bold] ", end="")
                console.print(str(turn.get("text") or ""), highlight=False, markup=False)
        filed = data.get("knowledge") or []
        if filed:
            console.rule("In Knowledge")
            for item in filed:
                console.print(
                    f"{item.get('project_name') or item.get('project_id')}: {item.get('status')}",
                    highlight=False,
                    markup=False,
                )

    emit(result, json_out=json_out, render=render, lines=_show_lines)


@app.command("send")
def send(
    link_id: str = typer.Argument(
        ..., help="A finished interview's link id (or its interview id when it has one link)."
    ),
    project: list[str] = typer.Option(  # noqa: B008
        ...,
        "--project",
        "-P",
        "--scope",
        help="A project id to file it in; repeatable (up to 10).",
        callback=project_ref_callback,
    ),
    json_out: bool = _json_opt,
) -> None:
    """Put a finished interview into each project's Knowledge."""
    with get_sdk() as sdk:
        filed = sdk.interviews.send_to_projects(_resolve_link_id(sdk, link_id), project)

    def render(data: Any) -> None:
        rows = data if isinstance(data, list) else [data]
        err_console.print(f"Sent to {len(rows)} project(s); Knowledge compiles it like an upload.")

    emit(filed, json_out=json_out, render=render)


@app.command("revoke")
def revoke(
    link_id: str = typer.Argument(
        ..., help="The link id to stop (or its interview id when it has one link)."
    ),
    json_out: bool = _json_opt,
) -> None:
    """Stop a link working; a finished participant keeps their result."""
    with get_sdk() as sdk:
        resolved = _resolve_link_id(sdk, link_id)
        result = sdk.interviews.revoke_link(resolved)
    # The route answers with no body; --json printed `null`. Say what happened.
    if not isinstance(result, dict):
        result = {"link_id": resolved, "revoked": True}

    def render(_data: Any) -> None:
        err_console.print(f"Revoked link {link_id}.", markup=False)

    emit(result, json_out=json_out, render=render, json_when_piped=False)


def register(root: typer.Typer) -> None:
    """Attach ``beakr interviews link`` (the root is unused)."""
    from beakr_cli.commands.interviews import app as interviews_app

    interviews_app.add_typer(app, name="link")
