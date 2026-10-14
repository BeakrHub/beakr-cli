"""``beakr interviews link create|ls|show|send|revoke``: send a link, read what they said.

One call creates the interview and its link; hand the link to the person however
you like (Beakr sends nothing). ``show`` prints what they answered and ``send``
puts a finished interview into one or more projects' Knowledge.
"""

from __future__ import annotations

from typing import Any

import typer

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
        )

    emit(link, json_out=json_out, render=render)


@app.command("ls")
def list_links(json_out: bool = _json_opt) -> None:
    """Your sendable interviews, newest first."""
    with get_sdk() as sdk:
        rows = sdk.interviews.sendables()

    def render(data: list[dict[str, Any]]) -> None:
        if not data:
            err_console.print("No interviews yet. Create one with `beakr interviews link create`.")
            return
        print_table(
            ["ID", "Title", "Links", "Created"],
            [
                [
                    str(r.get("id") or ""),
                    short(r.get("title"), 40),
                    str(len(r.get("links") or [])),
                    str(r.get("created_at") or "")[:16],
                ]
                for r in data
            ],
            title=f"Interviews ({len(data)})",
        )

    emit(rows, json_out=json_out, render=render)


@app.command("show")
def show(
    link_id: str = typer.Argument(..., help="The link id `create` printed."),
    json_out: bool = _json_opt,
) -> None:
    """What the person answered: status, the write-up (or summary) and the transcript."""
    with get_sdk() as sdk:
        result = sdk.interviews.link_result(link_id)

    def render(data: dict[str, Any]) -> None:
        console.print(f"Status: {data.get('status')}", highlight=False)
        note = data.get("write_up") or data.get("summary")
        if isinstance(note, str) and note:
            print_markdown(note)
        elif not data.get("completed_at"):
            err_console.print("Not finished yet.")

    emit(result, json_out=json_out, render=render)


@app.command("send")
def send(
    link_id: str = typer.Argument(..., help="A finished interview's link id."),
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
        filed = sdk.interviews.send_to_projects(link_id, project)

    def render(data: Any) -> None:
        rows = data if isinstance(data, list) else [data]
        err_console.print(f"Sent to {len(rows)} project(s); Knowledge compiles it like an upload.")

    emit(filed, json_out=json_out, render=render)


@app.command("revoke")
def revoke(
    link_id: str = typer.Argument(..., help="The link id to stop."),
    json_out: bool = _json_opt,
) -> None:
    """Stop a link working; a finished participant keeps their result."""
    with get_sdk() as sdk:
        result = sdk.interviews.revoke_link(link_id)

    def render(_data: Any) -> None:
        err_console.print(f"Revoked link {link_id}.")

    emit(result, json_out=json_out, render=render, json_when_piped=False)


def register(root: typer.Typer) -> None:
    """Attach ``beakr interviews link`` (the root is unused)."""
    from beakr_cli.commands.interviews import app as interviews_app

    interviews_app.add_typer(app, name="link")
