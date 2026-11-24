"""``beakr kb review interview``: turn a project's open review flags into one interview.

One plain-language question per open review flag: every kind by default, up to
30 (the rest are listed as left out); ``--kinds`` narrows it, ``--flags`` names
flags. Without a person it is a draft to edit and send in Beakr (the printed
``edit_url``). With ``--for``, ``--email`` or ``--user-id`` the interview also
gets that person's link, printed once; Beakr sends nothing. When it is answered,
each answer comes back on its flag (``kb review options`` shows them) as a
suggestion a person applies with the ``kb review`` resolve commands. Beakr
applies a person's own answer by itself only when they made the interview for
themself in their own Beakr session; one drafted here, with a key or a CLI
sign-in, never is, even when it names the person running it.

Over ``POST /v1/projects/{project_id}/interviews/from-reviews``
(:mod:`beakr_cli.review_interview`). The command joins the ``kb review`` group
when another module has made one, and makes it otherwise, so it sits beside the
review-flag commands without sharing a module with them.
"""

from __future__ import annotations

from typing import Any

import typer

from beakr_cli import review_interview
from beakr_cli.commands.kb import app as kb_app
from beakr_cli.commands.project_knowledge import _require_project
from beakr_cli.compact import cell
from beakr_cli.output import console, emit
from beakr_cli.project_ref import project_ref_callback
from beakr_cli.sdk import get_sdk

_project_opt = typer.Option(
    None,
    "--project",
    "-P",
    "--scope",
    "-s",
    help="Project id, slug or name (default: the bound or configured project).",
    callback=project_ref_callback,
)


def _split(value: str | None) -> list[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def _kinds(value: str | None) -> list[str]:
    kinds = _split(value)
    unknown = [kind for kind in kinds if kind not in review_interview.FLAG_KINDS]
    if unknown:
        raise typer.BadParameter(
            f"unknown kind {', '.join(unknown)}; use "
            f"{', '.join(review_interview.FLAG_KINDS)} (comma-separated)",
            param_hint="--kinds",
        )
    return kinds


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _skipped(data: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``(left out because the interview was full, skipped for another reason)``."""
    skipped = [s for s in data.get("skipped") or [] if isinstance(s, dict)]
    over = [s for s in skipped if s.get("reason") == review_interview.OVER_QUESTION_LIMIT]
    return over, [s for s in skipped if s not in over]


def resolve_steps(flag_id: str = "<flag_id>") -> list[str]:
    """How a flag is resolved from its answer, with the ``kb review`` commands."""
    return [
        "When it is answered, each answer is attached to its flag as a suggestion "
        "(interview_answers). An interview drafted here is never applied by itself: a "
        "person resolves the flag:",
        f"  beakr kb review options {flag_id}            # the answers and drafted resolutions",
        f"  beakr kb review resolve {flag_id} --option <the option an answer points to>",
    ]


def _render(data: dict[str, Any]) -> None:
    def say(line: str) -> None:
        console.print(line, highlight=False, markup=False, soft_wrap=True)

    interview = _dict(data.get("interview"))
    link = _dict(data.get("link"))
    over, skipped = _skipped(data)
    questions = [q for q in data.get("questions") or [] if isinstance(q, dict)]
    if not interview:
        say(f"Nothing drafted: {data.get('message') or data.get('empty_reason') or 'no flags'}")
    else:
        say(
            f'{"Made" if link else "Drafted"} "{interview.get("title")}" '
            f"(interview {interview.get('id')}): {len(questions)} question(s), one per "
            "review flag."
        )
        if data.get("questions_written_by") == "template":
            say("Some questions were built from their flag's text, not written by the model.")
        for question in questions:
            say("")
            say(f"{question.get('number')}. {question.get('question')}")
            say(
                f"   flag {question.get('proposal_id')} · {question.get('kind')} · "
                f"{question.get('page_title')}"
            )
            if question.get("reason"):
                say(f"   {question.get('reason')}")
    if over:
        say("")
        say(
            f"Left out ({len(over)} open flag(s) past the question limit; ask about them "
            "with --flags or --max-questions): "
            + ", ".join(str(s.get("proposal_id")) for s in over)
        )
    if skipped:
        say("")
        say(
            "Skipped: "
            + "; ".join(f"{s.get('proposal_id')} ({s.get('reason')})" for s in skipped)
            + "."
        )
    if not interview:
        return
    say("")
    if link:
        say(f"Link for {link.get('label')}, shown only once (Beakr sends nothing; send it):")
        if data.get("link_url"):
            say(f"  {data.get('link_url')}")
        else:
            say("  (not shown again: this answer is a replay of the call that made it)")
        say(f"Link id {link.get('link_id')}, expires {link.get('expires_at')}.")
        if link.get("result_url"):
            say(f"Result: {link.get('result_url')}")
        say(f"Follow it with `beakr interviews link show {link.get('link_id')}`.")
    else:
        say(f"Nothing was sent. Edit and send it in Beakr: {data.get('edit_url')}")
    say("")
    first = str(questions[0].get("proposal_id")) if len(questions) == 1 else "<flag_id>"
    for line in resolve_steps(first):
        say(line)


def _lines(data: dict[str, Any]) -> list[str]:
    interview = _dict(data.get("interview"))
    link = _dict(data.get("link"))
    out: list[str] = []
    if interview:
        fields = [
            "interview",
            cell(interview.get("id") or "-"),
            cell(interview.get("title") or "-"),
            f"questions={len(data.get('questions') or [])}",
            f"written_by={data.get('questions_written_by') or '-'}",
            f"edit_url={data.get('edit_url') or '-'}",
        ]
        if link:
            fields += [
                f"link_id={link.get('link_id') or '-'}",
                f"for={cell(link.get('label') or '-')}",
                f"link_url={data.get('link_url') or '-'}",
                f"expires_at={link.get('expires_at') or '-'}",
                f"result_url={link.get('result_url') or '-'}",
            ]
        out.append("\t".join(fields))
        for question in data.get("questions") or []:
            if not isinstance(question, dict):
                continue
            out.append(
                "\t".join(
                    [
                        str(question.get("number")),
                        cell(question.get("proposal_id") or "-"),
                        cell(question.get("kind") or "-"),
                        cell(question.get("page_title") or "-"),
                        cell(question.get("question") or ""),
                        f"reason={cell(question.get('reason') or '-')}",
                    ]
                )
            )
    else:
        out.append(
            "\t".join(
                [
                    "nothing_drafted",
                    cell(data.get("empty_reason") or "-"),
                    cell(data.get("message") or ""),
                ]
            )
        )
    for skipped in data.get("skipped") or []:
        if isinstance(skipped, dict):
            out.append(
                f"skipped\t{cell(skipped.get('proposal_id') or '-')}\t"
                f"{cell(skipped.get('reason') or '-')}"
            )
    return out


def _hint(data: dict[str, Any]) -> str | None:
    link = _dict(data.get("link"))
    if link.get("link_id"):
        return f"beakr interviews link show {link['link_id']}"
    return None


def interview(
    flags: str | None = typer.Option(
        None,
        "--flags",
        help="Review flag (proposal) ids to ask about, comma-separated, in order (up to 100). "
        "Default: every open review flag, newest first.",
    ),
    kinds: str | None = typer.Option(
        None,
        "--kinds",
        help="Ask only about flags of these kinds, comma-separated: contradiction, "
        "ambiguity, unverifiable, likely_error, other. Ignored with --flags.",
    ),
    max_questions: int | None = typer.Option(
        None,
        "--max-questions",
        min=1,
        max=review_interview.MAX_QUESTIONS,
        help="The most flags to ask about, one question each (default 30); the rest are "
        "listed as left out.",
    ),
    title: str | None = typer.Option(None, "--title", help="The interview's title."),
    goals: str | None = typer.Option(None, "--goals", help="The interview's goals."),
    name: str | None = typer.Option(
        None, "--for", help="The person it is for, by name: makes their link (printed once)."
    ),
    email: str | None = typer.Option(
        None, "--email", help="The person's email, shown beside the link; Beakr sends nothing."
    ),
    user_id: str | None = typer.Option(
        None, "--user-id", help="A member of your organization (user id): makes their link."
    ),
    expires_in_days: int | None = typer.Option(
        None,
        "--expires-in-days",
        min=1,
        max=90,
        help="How long the person's link works, in days (default 14).",
    ),
    project: str | None = _project_opt,
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
) -> None:
    """Turn the project's open review flags into one interview, one question per flag.

    \b
    Without --for, --email or --user-id it is a draft: edit and send it in
    Beakr at the printed link. With any of them the interview also gets that
    person's link, printed once; Beakr sends nothing, so deliver it yourself.
    Each answer comes back on its flag (`beakr kb review options` shows it)
    as a suggestion, applied with `beakr kb review resolve`; an interview
    drafted here is never applied by itself. Needs interviews:write and kb:read.
    Exit codes: 0 made (also when there was nothing to ask), 3 not allowed,
    4 no such project or person.
    """
    participant = {"user_id": user_id, "name": name, "email": email}
    if expires_in_days is not None and not any((user_id, name, email)):
        raise typer.BadParameter(
            "only a person's link expires; give --for, --email or --user-id",
            param_hint="--expires-in-days",
        )
    body = review_interview.request_body(
        proposal_ids=_split(flags),
        kinds=_kinds(kinds),
        max_questions=max_questions,
        participant=participant,
        link_expires_in_days=expires_in_days,
        title=title,
        goals=goals,
    )
    project_id = _require_project(project)
    with get_sdk() as sdk:
        answer = review_interview.draft_from_reviews(sdk, project_id, body)
    data = answer if isinstance(answer, dict) else {}
    emit(data, json_out=json_out, render=_render, lines=_lines, hint=_hint)


def _review_group() -> typer.Typer:
    """``kb review``: the group another module made, else a new one."""
    for group in kb_app.registered_groups:
        if group.name == "review" and group.typer_instance is not None:
            return group.typer_instance
    app = typer.Typer(help="Review flags Knowledge raised.", no_args_is_help=True)
    kb_app.add_typer(app, name="review")
    return app


def register(root: typer.Typer) -> None:
    """Attach ``kb review interview`` (the root is unused)."""
    _review_group().command("interview")(interview)
