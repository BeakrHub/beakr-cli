"""``beakr kb review ls|options|resolve``: resolve the concerns Knowledge flagged.

A review flag is a proposal of type ``review``: a section Knowledge found
contradicted, unverifiable or unclear and could not settle. Accepting or
dismissing it (``beakr kb proposals``) leaves the page as it is; these commands
change the page and clear the flag:

- ``ls`` lists the pending flags;
- ``options`` shows a flag and Beakr's drafted resolutions, numbered, with what
  each changes (drafted once per page revision, then kept: up to two minutes the
  first time, at once after), each interview answer on it, and, for a flag
  already decided, how it was decided;
- ``resolve`` applies one: ``--option N`` (a drafted option, a section rewrite or
  a card-table row change, whichever it is), ``--edit`` (the section's text in
  $EDITOR), ``--body FILE`` (the section's new text from a file, ``-`` for
  stdin) or ``--outcome`` (a contradiction between cards' rows, settled on every
  card it lists).

``resolve`` pins the page revision it read (or ``--revision``), so a page that
changed meanwhile is refused (exit 5) with nothing applied: run ``options``
again. ``--option`` applies only an option ``options`` already showed for the
page as it is: drafts that had to be made afresh are refused the same way.
Where a person must accept proposals, only a person, or a key with the
``proposals:accept`` (reviewer) permission, may apply a resolution: any other key
or token is refused (exit 6), as it is for ``kb proposals accept``. A scheduled
job closes flags with a reviewer key.
"""

from __future__ import annotations

import sys
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import click
import typer
from beakr_sdk.errors import (
    ConflictError,
    NotFoundError,
    PersonReviewRequiredError,
    ValidationError,
)

from beakr_cli import scope_session
from beakr_cli.commands.kb import app as kb_app
from beakr_cli.compact import cell
from beakr_cli.output import (
    console,
    err_console,
    machine_output,
    output_mode,
    page_rows,
    print_table,
)
from beakr_cli.project_binding import resolve_project
from beakr_cli.project_ref import project_ref_callback
from beakr_cli.prompts import confirm_or_require_yes
from beakr_cli.proposal_ids import resolve_proposal_id
from beakr_cli.review_flags import decided_text, options_text, resolved_text
from beakr_cli.review_resolve import (
    OUTCOMES,
    PERSON_ONLY_CLI_FIX,
    PERSON_ONLY_MESSAGE,
    ResolveRefused,
    is_decided,
    option_request,
    section_request,
)
from beakr_cli.sdk import get_sdk

app = typer.Typer(help="Resolve the concerns Knowledge flagged for review.")

#: Drafting is a model call the engine bounds at about two minutes.
_DRAFT_TIMEOUT = 170.0
#: The last line of the shared text names the MCP tool; the CLI names its command.
_MCP_APPLY_LINE = "Apply one with resolve_review("
_LIST_PAGE = 200

_json_opt = typer.Option(False, "--json", "-j", help="Output raw JSON.")
_id_arg = typer.Argument(..., help="The flag's proposal id, or the start of it.")


def _base(proposal_id: str) -> str:
    return f"/v1/knowledge/wiki/proposals/{proposal_id}"


def _flag_id(sdk: Any, given: str) -> str:
    return resolve_proposal_id(sdk, given, pending_only=True)


def _changed(proposal_id: str, detail: str) -> ConflictError:
    return ConflictError(
        f"{detail} Nothing was applied.",
        code="conflict",
        fix=f"Run `beakr kb review options {proposal_id}` and choose again.",
    )


def _refused(refused: ResolveRefused) -> ValidationError:
    return ValidationError(refused.message, code=refused.code)


# ---------------------------------------------------------------------------
# ls
# ---------------------------------------------------------------------------


@app.command("ls")
def review_ls(
    project: str | None = typer.Option(
        None,
        "--project",
        "-P",
        "--scope",
        "-s",
        help="Project id, slug or name; defaults to the bound project.",
        callback=project_ref_callback,
    ),
    limit: int = typer.Option(50, "--limit", "-n", min=1, max=200, help="Flags to list."),
    json_out: bool = _json_opt,
) -> None:
    """List pending review flags, newest first."""
    project_id = resolve_project(project)
    flags: list[dict] = []
    cursor: str | None = None
    with get_sdk() as sdk:
        while len(flags) < limit:
            params = {
                "status": "pending",
                "proposal_type": "review",
                "limit": _LIST_PAGE,
                "cursor": cursor,
            }
            if project_id:
                params["project_id"] = project_id
            page = sdk.request(
                "GET",
                "/v1/knowledge/wiki/proposals",
                params={key: value for key, value in params.items() if value is not None},
            )
            rows = page_rows(page)
            # The engine filters by proposal_type; one that predates the filter
            # ignores it, so the listing is narrowed here too.
            flags += [row for row in rows if row.get("proposal_type") == "review"]
            cursor = page.get("next_cursor") if isinstance(page, dict) else None
            if not rows or not cursor or not page.get("has_more", True):
                break
    flags = flags[:limit]
    if machine_output(flags, json_out=json_out, lines=_flag_lines):
        return
    if not flags:
        console.print("No pending review flags.")
        return
    print_table(
        ["ID", "Page", "Why flagged"],
        [
            [str(f.get("id") or "")[:8], (f.get("page_title") or "")[:32], f.get("summary") or ""]
            for f in flags
        ],
        title="Review flags",
    )
    err_console.print(
        "See a flag's options with `beakr kb review options <id>`.", markup=False, highlight=False
    )


def _flag_lines(flags: list[dict]) -> list[str]:
    lines = []
    for flag in flags:
        columns = [str(flag.get("id") or "")]
        if flag.get("page_title"):
            columns.append(f"page={cell(flag['page_title'])}")
        if flag.get("summary"):
            columns.append(cell(flag["summary"], width=200))
        lines.append("\t".join(columns))
    return lines


# ---------------------------------------------------------------------------
# options
# ---------------------------------------------------------------------------


@app.command("options")
def review_options(proposal_id: str = _id_arg, json_out: bool = _json_opt) -> None:
    """Show a flag and Beakr's drafted resolutions, numbered, with what each changes.

    The first call for a page revision drafts them (up to two minutes); later
    calls return the same options at once until the page changes. A flag an
    interview asked about also lists each answer and whether the answers agree;
    a flag already resolved or dismissed says how it was decided instead.
    """
    with get_sdk(timeout=_DRAFT_TIMEOUT) as sdk:
        proposal_id = _any_flag_id(sdk, proposal_id)
        flag = sdk.request("GET", _base(proposal_id)) or {}
        if is_decided(flag):
            if not machine_output(flag, json_out=json_out, lines=_decided_lines):
                _print_lines(_decided_lines(flag))
            return
        conflict = sdk.request("GET", f"{_base(proposal_id)}/conflict") or {}
        if output_mode(json_out) == "human":
            err_console.print("Drafting options (up to two minutes the first time)...")
        drafted = sdk.request("POST", f"{_base(proposal_id)}/resolutions") or {}
        # Drafting may have matched an interview answer to the options as they are now.
        flag = sdk.request("GET", _base(proposal_id)) or {}
    data = {**drafted, "flag": conflict, **_answer_fields(flag)}
    if machine_output(data, json_out=json_out, lines=_options_lines):
        return
    _print_lines(_options_lines(data))


def _any_flag_id(sdk: Any, given: str) -> str:
    """The flag ``given`` names: a pending one first, else one already decided.

    ``options`` says how a decided flag was settled, so a prefix may name one; the
    pending flags are searched alone first, since those are the ones acted on.
    """
    try:
        return resolve_proposal_id(sdk, given, pending_only=True)
    except NotFoundError:
        return resolve_proposal_id(sdk, given)


def _answer_fields(flag: dict) -> dict[str, Any]:
    """The interview answers on the proposal read, when the engine sends them."""
    return {
        key: flag[key]
        for key in ("interview_answers", "interview_answers_note")
        if flag.get(key) is not None
    }


def _print_lines(lines: list[str]) -> None:
    for line in lines:
        console.print(line, markup=False, highlight=False, soft_wrap=True)


def _decided_lines(flag: dict) -> list[str]:
    return decided_text(flag).splitlines()


def _options_lines(data: dict) -> list[str]:
    """The shared text, with the CLI's own way of applying an option in place of the tool's."""
    proposal_id = data.get("proposal_id")
    lines = []
    for line in options_text(data.get("flag") or {}, data, data).splitlines():
        if line.startswith(_MCP_APPLY_LINE):
            line = f"Apply one with `beakr kb review resolve {proposal_id} --option <number>`."
        lines.append(line)
    lines.append(f"Or write the section yourself: `beakr kb review resolve {proposal_id} --edit`.")
    return lines


# ---------------------------------------------------------------------------
# resolve
# ---------------------------------------------------------------------------


@app.command("resolve")
def review_resolve(
    proposal_id: str = _id_arg,
    option: str | None = typer.Option(
        None, "--option", "-o", help="The drafted option to apply: its number or letter."
    ),
    edit: bool = typer.Option(
        False, "--edit", "-e", help="Write the flagged section yourself, in $EDITOR."
    ),
    body: str | None = typer.Option(
        None, "--body", help="The flagged section's new Markdown from a file ('-' for stdin)."
    ),
    outcome: str | None = typer.Option(
        None,
        "--outcome",
        help="For a contradiction between cards' rows, settled on every card it lists: "
        + ", ".join(OUTCOMES)
        + ".",
    ),
    revision: int | None = typer.Option(
        None,
        "--revision",
        min=1,
        help="The page revision you chose against (as `options` printed it); defaults to "
        "the page's revision now.",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
    json_out: bool = _json_opt,
) -> None:
    """Resolve a review flag: apply a drafted option, your own text, or an outcome.

    The page changes at once and the flag clears. A page that changed since the
    revision this reads (or --revision) is refused with nothing applied (exit 5).
    Where a person must accept proposals, a person, or a key with the
    proposals:accept (reviewer) permission, must apply it (exit 6 otherwise).
    """
    ways = [
        name
        for name, given in (
            ("--option", option),
            ("--edit", edit),
            ("--body", body),
            ("--outcome", outcome),
        )
        if given
    ]
    if len(ways) != 1:
        raise ValidationError(
            "Choose exactly one of --option, --edit, --body or --outcome.",
            fix=f"See the options with `beakr kb review options {proposal_id}`.",
        )
    if outcome is not None and outcome not in OUTCOMES:
        raise ValidationError(f"--outcome must be one of {', '.join(OUTCOMES)}.")
    # From a plain terminal it is the person's own review: their key, joined to no
    # session (the API treats a request inside a session as an agent's, which may not
    # resolve where a person must). Inside an agent conversation it stays the agent's.
    as_person = (
        scope_session.person_credential(unjoined=True)
        if scope_session.agent_session_id() is None
        else nullcontext()
    )
    with as_person, get_sdk(timeout=_DRAFT_TIMEOUT) as sdk:
        proposal_id = _flag_id(sdk, proposal_id)
        base = _base(proposal_id)
        if outcome is not None:
            confirm_or_require_yes(f"Settle review {proposal_id} as {outcome} on every card?", yes)
            path, request = "/resolve-cards", {"outcome": outcome}
        else:
            conflict = sdk.request("GET", f"{base}/conflict") or {}
            current = conflict.get("current_revision")
            if revision is not None and revision != current:
                raise _changed(
                    proposal_id,
                    f"The page changed since revision {revision} (it is at {current} now).",
                )
            pinned = current
            if option is not None:
                path, request = _option_choice(sdk, proposal_id, conflict, option, pinned)
                prompt = f"Apply option {request.get('option_id')} to review flag {proposal_id}?"
            else:
                text = _edited(conflict) if edit else _read_body(body or "-")
                if text is None:
                    err_console.print("No changes; the flag was left as it was.")
                    raise typer.Exit(1)
                path, request = section_request(conflict, text, pinned)
                prompt = f"Rewrite the flagged section and resolve review flag {proposal_id}?"
            confirm_or_require_yes(prompt, yes)
        try:
            result = sdk.request("POST", base + path, json=request) or {}
        except ConflictError as exc:
            raise _changed(proposal_id, "The page changed since you chose.") from exc
        except PersonReviewRequiredError as exc:
            raise PersonReviewRequiredError(
                PERSON_ONLY_MESSAGE,
                status=403,
                code="person_review_required",
                request_id=exc.request_id,
                fix=PERSON_ONLY_CLI_FIX,
            ) from exc
    if not machine_output(result, json_out=json_out):
        console.print(resolved_text(result), markup=False, highlight=False, soft_wrap=True)


def _option_choice(
    sdk: Any, proposal_id: str, conflict: dict, option: str, pinned: Any
) -> tuple[str, dict[str, Any]]:
    """The drafted option as ``options`` showed it for the page as it is, and where it goes.

    Drafts that had to be made by this call (``cached: false``) were never shown,
    so they are not applied: ``options`` shows them now, at once.
    """
    drafted = sdk.request("POST", f"{_base(proposal_id)}/resolutions") or {}
    if drafted.get("current_revision") != pinned:
        raise _changed(proposal_id, "The page changed while the options were read.")
    if not drafted.get("cached"):
        raise _changed(
            proposal_id,
            "Beakr drafted these options just now, for the page as it is, so they have not "
            "been shown yet.",
        )
    try:
        return option_request(proposal_id, conflict, drafted, option, pinned)
    except ResolveRefused as refused:
        raise _refused(refused) from refused


def _edited(conflict: dict) -> str | None:
    """The flagged section as the person edits it in $EDITOR; None when left unchanged."""
    if conflict.get("anchor") not in (None, "section") or not conflict.get("section_id"):
        raise ValidationError(
            "This flag names no section of the page as it is now, so there is nothing to edit.",
            fix="Read the page, then accept or dismiss the flag with `beakr kb proposals`.",
        )
    if conflict.get("section_kind") == "card_table":
        raise ValidationError(
            "This flag is on a card's table, whose rows change only through row operations.",
            fix="Apply a drafted row option with --option.",
        )
    original = conflict.get("section_content") or ""
    edited = click.edit(original, extension=".md")
    if edited is None or edited.strip() == original.strip() or not edited.strip():
        return None
    return edited


def _read_body(source: str) -> str | None:
    text = sys.stdin.read() if source == "-" else Path(source).read_text()
    return text if text.strip() else None


def register(root: typer.Typer) -> None:
    """Attach ``kb review`` (the root is unused)."""
    kb_app.add_typer(app, name="review")
