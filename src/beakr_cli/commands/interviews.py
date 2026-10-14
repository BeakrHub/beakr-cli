"""``beakr interviews request|draft|ls|show``: ask a person, read the note it produced.

``request`` writes an interview (title, goals, key questions) for one person.
With ``--to`` Beakr delivers it (a teammate's inbox, or an email with the link);
without it you get a link to send them yourself. ``ls`` lists your interviews and sessions,
``show`` prints a session's note or, with ``--link``, what a person answered.
``draft`` asks Beakr's interview maker to propose the title, goals and questions.
"""

from __future__ import annotations

import re
from typing import Any

import typer

from beakr_cli.app_url import resolve_app_url
from beakr_cli.output import console, emit, err_console, print_markdown, print_table, short
from beakr_cli.project_ref import project_ref_callback
from beakr_cli.sdk import get_sdk

app = typer.Typer(help="Interviews: request one from a person, list them, read the result.")

_json_opt = typer.Option(False, "--json", "-j", help="Output raw JSON.")


_UUID = re.compile(r"^[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")


def _recipient(to: str) -> dict[str, str]:
    """``--to``: an email address (anyone) or a user id (a member of your organization)."""
    value = to.strip()
    if "@" in value:
        return {"email": value}
    if _UUID.match(value):
        return {"user_id": value}
    raise typer.BadParameter(
        "Give an email address, or a teammate's user id (see `beakr members ls`).",
        param_hint="--to",
    )


@app.command("request")
def request_interview(
    title: str = typer.Argument(..., help="What the interview is about."),
    goal: str | None = typer.Option(None, "--goal", "-g", help="What you want to learn."),
    question: list[str] = typer.Option(  # noqa: B008
        [], "--question", "-q", help="A key question to cover; repeatable."
    ),
    to: str | None = typer.Option(
        None,
        "--to",
        help=(
            "Have Beakr deliver it: an email address (they get a link by email) or a "
            "teammate's user id (it lands in their interview inbox; needs --project)."
        ),
    ),
    project: str | None = typer.Option(
        None,
        "--project",
        "-P",
        "--scope",
        help="With --to USER_ID: the project the interview fills.",
        callback=project_ref_callback,
    ),
    due: str | None = typer.Option(None, "--due", help="With --to: answers wanted by YYYY-MM-DD."),
    message: str | None = typer.Option(
        None, "--message", "-m", help="With --to: a note shown to the person."
    ),
    person: str | None = typer.Option(
        None, "--for", help="Label the link with who it is for (e.g. their name)."
    ),
    expires_in_days: int | None = typer.Option(None, "--expires-in-days", min=1, max=90),
    json_out: bool = _json_opt,
) -> None:
    """Ask a person for an interview.

    With --to, Beakr delivers it: a teammate (user id) gets it in their
    interview inbox, anyone else (email) gets a link by email. Without --to,
    you get a link to send yourself.
    """
    if to is None:
        if project or due or message:
            raise typer.BadParameter("--project, --due and --message need --to.")
        _request_by_link(title, goal, question, person, expires_in_days, json_out)
        return
    if person or expires_in_days is not None:
        raise typer.BadParameter("--for and --expires-in-days are for a link you send; drop --to.")
    recipient = _recipient(to)
    if "user_id" in recipient and not project:
        raise typer.BadParameter("Asking a teammate needs --project.", param_hint="--project")
    if "email" in recipient and project:
        raise typer.BadParameter(
            "An emailed interview has no project; drop --project.", param_hint="--project"
        )
    with get_sdk() as sdk:
        result = sdk.interviews.send_request(
            recipient.get("email") or recipient["user_id"],
            project_id=project,
            title=title,
            goals=goal,
            questions=list(question) or None,
            due=due,
            message=message,
        )

    def render(data: dict[str, Any]) -> None:
        if data.get("delivery") == "inbox":
            console.print(
                f"Asked {to}: the request is in their interview inbox "
                f"(request {data.get('request_id')}).",
                highlight=False,
            )
            return
        status = data.get("email_status")
        if status == "queued":
            console.print(f"Emailed {to} a link to the interview.", highlight=False)
        else:
            path = str(data.get("path") or "")
            url = f"{resolve_app_url().rstrip('/')}{path}" if path.startswith("/") else path
            reason = (
                "this is a sandbox organization, so no email was sent"
                if status == "not_sent_sandbox"
                else "the email may not have been sent"
            )
            console.print(f"Made a link for {to}, but {reason}.", highlight=False)
            if url:
                console.print(f"Send this link yourself (shown once): {url}", highlight=False)
        err_console.print(
            f"Read the answers with `beakr interviews show --link {data.get('link_id')}`."
        )

    emit(result, json_out=json_out, render=render)


def _request_by_link(
    title: str,
    goal: str | None,
    question: list[str],
    person: str | None,
    expires_in_days: int | None,
    json_out: bool,
) -> None:
    """Create an interview and a link to send to the person who should answer it."""
    with get_sdk() as sdk:
        sendable = sdk.interviews.create_sendable(
            title, goals=goal, key_questions=list(question) or None
        )
        link = sdk.interviews.link_for(
            str(sendable["id"]), label=person, expires_in_days=expires_in_days
        )
    result = {"interview": sendable, "link": link}

    def render(data: dict[str, Any]) -> None:
        path = str(data["link"].get("path") or "")
        url = f"{resolve_app_url().rstrip('/')}{path}" if path.startswith("/") else path
        console.print(f"Interview {sendable.get('id')}: {title}", highlight=False)
        console.print(f"Send this link (shown once): {url}", highlight=False)
        err_console.print(
            f"Read the answers with `beakr interviews show --link {data['link'].get('link_id')}`."
        )

    emit(result, json_out=json_out, render=render)


@app.command("draft")
def draft_interview(
    message: str = typer.Argument(..., help="What you want to find out, in words."),
    title: str | None = typer.Option(None, "--title", help="The draft's title so far."),
    goal: str | None = typer.Option(None, "--goal", "-g", help="The draft's goals so far."),
    question: list[str] = typer.Option(  # noqa: B008
        [], "--question", "-q", help="A key question already in the draft; repeatable."
    ),
    json_out: bool = _json_opt,
) -> None:
    """Beakr drafts an interview with you; nothing is saved.

    Prints the maker's reply and the changes it proposes. Create the interview
    with `beakr interviews request` using the title and questions you keep.
    """
    draft = {
        key: value
        for key, value in {
            "title": title,
            "goals": goal,
            "key_questions": list(question) or None,
        }.items()
        if value is not None
    }
    with get_sdk() as sdk:
        drafted = sdk.interviews.draft_sendable(message, draft=draft or None)

    def render(data: dict[str, Any]) -> None:
        console.print(str(data.get("reply") or ""), highlight=False)
        changes = data.get("changes") or {}
        for key in ("title", "goals", "supporting_info"):
            if changes.get(key):
                console.print(f"{key}: {changes[key]}", highlight=False)
        for item in changes.get("key_questions") or []:
            console.print(f"  - {item}", highlight=False)
        pending = data.get("pending_files") or []
        if pending:
            err_console.print(f"Still reading: {', '.join(map(str, pending))}")

    emit(drafted, json_out=json_out, render=render)


@app.command("ls")
def list_interviews(
    project: str | None = typer.Option(
        None,
        "--project",
        "-P",
        "--scope",
        help="Sessions in a project.",
        callback=project_ref_callback,
    ),
    json_out: bool = _json_opt,
) -> None:
    """Interviews you sent, your interview sessions, and requests waiting for you."""
    with get_sdk() as sdk:
        sendables = sdk.interviews.sendables()
        sessions = sdk.interviews.sessions(project_id=project)
        requests = sdk.interviews.requests()
    result = {"sent": sendables, "sessions": sessions, "requests": requests}

    def rows_of(value: Any, *keys: str) -> list[dict[str, Any]]:
        if isinstance(value, list):
            return value
        for key in keys:
            if isinstance(value, dict) and isinstance(value.get(key), list):
                return value[key]
        return []

    def render(data: dict[str, Any]) -> None:
        sent = rows_of(data["sent"], "data", "sendables")
        held = rows_of(data["sessions"], "sessions", "data")
        waiting = rows_of(data["requests"], "requests", "data")
        if sent:
            print_table(
                ["Interview ID", "Title", "Links", "Created"],
                [
                    [
                        str(s.get("id")),
                        short(s.get("title"), 40),
                        str(len(s.get("links") or [])),
                        str(s.get("created_at") or "")[:10],
                    ]
                    for s in sent
                ],
                title="Sent",
            )
        if held:
            print_table(
                ["Session ID", "Title", "Status", "Updated"],
                [
                    [
                        str(s.get("session_id") or s.get("id")),
                        short(s.get("title"), 40),
                        str(s.get("status") or ""),
                        str(s.get("updated_at") or s.get("created_at") or "")[:10],
                    ]
                    for s in held
                ],
                title="Sessions",
            )
        if waiting:
            print_table(
                ["Request ID", "Title", "From"],
                [
                    [
                        str(r.get("id")),
                        short(r.get("title"), 40),
                        short(r.get("requester_name") or r.get("created_by"), 24),
                    ]
                    for r in waiting
                ],
                title="Waiting for you",
            )
        if not (sent or held or waiting):
            err_console.print("No interviews. Ask someone with `beakr interviews request TITLE`.")

    emit(result, json_out=json_out, render=render)


@app.command("show")
def show_interview(
    session_id: str | None = typer.Argument(None, help="Session id from `beakr interviews ls`."),
    link: str | None = typer.Option(None, "--link", help="A sent link's id: what they answered."),
    json_out: bool = _json_opt,
) -> None:
    """A session's note, or with --link the result of an interview you sent."""
    if bool(session_id) == bool(link):
        raise typer.BadParameter("Give a SESSION_ID or --link LINK_ID.")
    with get_sdk() as sdk:
        result = sdk.interviews.link_result(link) if link else sdk.interviews.session(session_id)

    def render(data: dict[str, Any]) -> None:
        if link:
            console.print(f"[bold]Link {link}[/bold]: {data.get('status')}", highlight=False)
            print_markdown(str(data.get("summary") or "(no answers yet)"))
            uploads = data.get("uploads") or []
            if uploads:
                console.print(f"{len(uploads)} file(s) uploaded by the person.")
            return
        console.print(
            f"[bold]{data.get('title') or session_id}[/bold] ({data.get('status')}, "
            f"{data.get('proposal_count') or 0} Knowledge proposal(s))",
            highlight=False,
        )
        for turn in data.get("prior_turns") or []:
            speaker = "Q" if turn.get("role") in {"assistant", "interviewer"} else "A"
            console.print(f"{speaker}: {turn.get('text') or ''}", highlight=False)

    emit(result, json_out=json_out, render=render)


def register(root: typer.Typer) -> None:
    root.add_typer(app, name="interviews")
