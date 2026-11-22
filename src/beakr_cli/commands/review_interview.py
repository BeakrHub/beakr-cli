"""``beakr kb review interview``: draft an interview from a project's open review flags.

One plain-language question per open review flag (contradictions by default;
``--include`` adds other kinds, ``--flags`` names flags), saved as a draft
interview for a person who knows the answers. Nothing is sent: the person edits
and sends it in Beakr (the printed ``edit_url``). When it is answered, each answer
comes back on its flag as a suggestion a person applies.

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
    unknown = [kind for kind in kinds if kind not in review_interview.INCLUDE_KINDS]
    if unknown:
        raise typer.BadParameter(
            f"unknown kind {', '.join(unknown)}; use "
            f"{', '.join(review_interview.INCLUDE_KINDS)} (comma-separated)",
            param_hint="--include",
        )
    return kinds


def _render(data: dict[str, Any]) -> None:
    def say(line: str) -> None:
        console.print(line, highlight=False, markup=False, soft_wrap=True)

    interview = data.get("interview") if isinstance(data.get("interview"), dict) else None
    skipped = [s for s in data.get("skipped") or [] if isinstance(s, dict)]
    if not interview:
        say(f"Nothing drafted: {data.get('message') or data.get('empty_reason') or 'no flags'}")
    else:
        questions = [q for q in data.get("questions") or [] if isinstance(q, dict)]
        say(
            f'Drafted "{interview.get("title")}" (interview {interview.get("id")}): '
            f"{len(questions)} question(s), one per review flag. Nothing was sent."
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
    if skipped:
        say("")
        say(
            "Skipped: "
            + "; ".join(f"{s.get('proposal_id')} ({s.get('reason')})" for s in skipped)
            + "."
        )
    if interview:
        say("")
        say(f"Edit and send it in Beakr: {data.get('edit_url')}")
        say("Answers come back on each flag as suggestions; a person applies them.")


def _lines(data: dict[str, Any]) -> list[str]:
    interview = data.get("interview") if isinstance(data.get("interview"), dict) else None
    out: list[str] = []
    if interview:
        out.append(
            "\t".join(
                [
                    "interview",
                    cell(interview.get("id") or "-"),
                    cell(interview.get("title") or "-"),
                    f"questions={len(data.get('questions') or [])}",
                    f"written_by={data.get('questions_written_by') or '-'}",
                    f"edit_url={data.get('edit_url') or '-'}",
                ]
            )
        )
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


def interview(
    flags: str | None = typer.Option(
        None,
        "--flags",
        help="Review flag (proposal) ids to ask about, comma-separated, in order. "
        "Default: the project's open conflict flags, newest first.",
    ),
    include: str | None = typer.Option(
        None,
        "--include",
        help="Review kinds to ask about besides conflicts, comma-separated: "
        "ambiguity, unverifiable, likely_error, or all. Ignored with --flags.",
    ),
    participant: str | None = typer.Option(
        None,
        "--for",
        help="Who the questions are for (a name or a role); wording only, nothing is sent.",
    ),
    title: str | None = typer.Option(None, "--title", help="The interview's title."),
    goals: str | None = typer.Option(None, "--goals", help="The interview's goals."),
    max_questions: int | None = typer.Option(
        None,
        "--max-questions",
        min=1,
        max=review_interview.MAX_QUESTIONS,
        help="The most flags to ask about, one question each (default 15).",
    ),
    project: str | None = _project_opt,
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
) -> None:
    """Draft an interview from the project's open review flags. Nothing is sent.

    \b
    One question per flag, saved as a draft interview; edit and send it in
    Beakr at the printed link. Each answer comes back on its flag as a
    suggestion a person applies. Needs interviews:write and kb:read.
    Exit codes: 0 drafted (also when there was nothing to ask), 3 not allowed,
    4 no such project.
    """
    body = review_interview.request_body(
        proposal_ids=_split(flags),
        include=_kinds(include),
        max_questions=max_questions,
        participant=participant,
        title=title,
        goals=goals,
    )
    project_id = _require_project(project)
    with get_sdk() as sdk:
        answer = review_interview.draft_from_reviews(sdk, project_id, body)
    data = answer if isinstance(answer, dict) else {}
    emit(data, json_out=json_out, render=_render, lines=_lines)


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
