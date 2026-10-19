"""``beakr usage`` and ``beakr trace``.

``usage`` reports model calls, cost and tokens grouped by key, project, model
or day (``--org`` for the whole organization, admins only). ``trace`` shows how
a Knowledge page, an output, an agent run or one Knowledge row (a single value
of a card: ``beakr trace row <card> --row <index-or-key>``) was made.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import typer
from beakr_sdk.errors import NotFoundError, PermissionDeniedError, ValidationError

from beakr_cli.output import console, emit, print_table, short
from beakr_cli.project_ref import project_ref_callback
from beakr_cli.sdk import get_sdk

_json_opt = typer.Option(False, "--json", "-j", help="Output raw JSON.")
_GROUPS = ("key", "project", "model", "day")
_KINDS = ("page", "output", "run")


def usage(
    group_by: str = typer.Option("day", "--group-by", "-g", help="key, project, model or day."),
    start: str | None = typer.Option(None, "--from", help="Start date (ISO); default 30 days ago."),
    end: str | None = typer.Option(None, "--to", help="End date (ISO); default now."),
    org: bool = typer.Option(False, "--org", help="The whole organization (admins)."),
    json_out: bool = _json_opt,
) -> None:
    """Model usage: calls, cost and tokens per key, project, model or day."""
    if group_by not in _GROUPS:
        raise typer.BadParameter(f"--group-by must be one of {', '.join(_GROUPS)}")
    with get_sdk() as sdk:
        report = sdk.usage.get(
            group_by=group_by,  # type: ignore[arg-type]
            start=start,
            end=end,
            scope="org" if org else "self",
        )

    def render(data: dict[str, Any]) -> None:
        print_table(
            [group_by.title(), "Calls", "Cost (USD)", "Tokens"],
            [
                [
                    short(row.get(group_by) if row.get(group_by) is not None else "(none)", 44),
                    str(row.get("calls", 0)),
                    f"{row.get('cost_usd', 0):.4f}",
                    str(row.get("tokens", 0)),
                ]
                for row in data.get("data") or []
            ],
            title=f"Usage {str(data.get('from'))[:10]} to {str(data.get('to'))[:10]} "
            f"({data.get('scope')})",
        )
        total = data.get("total") or {}
        console.print(
            f"Total: {total.get('calls', 0)} calls, ${total.get('cost_usd', 0):.4f}, "
            f"{total.get('tokens', 0)} tokens",
            highlight=False,
            markup=False,
        )

    emit(report, json_out=json_out, render=render)


def _trace_by_id(sdk: Any, item_id: str) -> dict[str, Any]:
    """Find the one page, output or run with this id (``beakr trace <id>``).

    Each kind needs its own scope (pages ``kb:read``, outputs also
    ``outputs:read``, runs also ``agent:run``). A kind the credential may not
    trace is skipped rather than failing the whole lookup: a ``kb:read`` key
    tracing one of its pages must not be told to add ``agent:run`` because
    the run probe was refused (audit C087). Authentication, step-up and server
    errors still surface.
    """
    found: list[tuple[str, dict[str, Any]]] = []
    refused: PermissionDeniedError | None = None
    for kind in _KINDS:
        try:
            found.append((kind, sdk.trace.get(kind, item_id)))  # type: ignore[arg-type]
        except (NotFoundError, ValidationError):
            continue
        except PermissionDeniedError as exc:
            if exc.code != "insufficient_scope":
                raise
            refused = refused or exc
    if not found:
        if refused is not None:
            # It may be a kind this credential cannot trace; say which scope
            # that would take instead of claiming nothing has this id.
            raise refused
        raise NotFoundError(f"No page, output or run has the id {item_id}.")
    if len(found) > 1:
        kinds = ", ".join(kind for kind, _ in found)
        raise typer.BadParameter(
            f"{item_id} matches more than one kind ({kinds}); "
            f"run `beakr trace <kind> {item_id}` with one of them."
        )
    return found[0][1]


def trace(
    kind_or_id: str = typer.Argument(
        ...,
        metavar="[KIND] ID",
        help="An id, or a kind (page, output, run or row) and then its id (row: the card).",
    ),
    item_id: str | None = typer.Argument(None, hidden=True),
    table: str = typer.Option("facts", "--table", help="row: facts, connections or findings."),
    row: str | None = typer.Option(
        None,
        "--row",
        help="row: the row's 1-based index in the table, or its Attribute, Relation or text.",
    ),
    value: str | None = typer.Option(None, "--value", help="row: narrow a --row text match."),
    project: str | None = typer.Option(
        None,
        "--project",
        "-P",
        "--scope",
        help="row: the project to find a card title in.",
        callback=project_ref_callback,
    ),
    project_p: str | None = typer.Option(None, "-p", hidden=True, callback=project_ref_callback),
    json_out: bool = _json_opt,
) -> None:
    """How a page, output, run or one Knowledge row was made.

    `beakr trace <id>` finds which kind the id is; `beakr trace <kind> <id>`
    names it. `beakr trace row <card> --row Temperature` traces one value of a
    card (title or id): its sources and versions, the proposal and commit that
    wrote it, who and through which credential, client and agent, its run, and
    its earlier values.
    """
    # -p was this option's short flag before -P everywhere; still accepted.
    project = project or project_p

    if kind_or_id == "row":
        if item_id is None:
            raise typer.BadParameter("name the card: `beakr trace row <card> --row <row>`")
        result = _trace_row(item_id, table=table, row=row, value=value, project=project)
        emit(result, json_out=json_out, render=_render_row)
        return
    with get_sdk() as sdk:
        if item_id is None:
            result = _trace_by_id(sdk, kind_or_id)
        else:
            if kind_or_id not in _KINDS:
                raise typer.BadParameter(f"kind must be one of {', '.join((*_KINDS, 'row'))}")
            result = sdk.trace.get(kind_or_id, item_id)  # type: ignore[arg-type]

    def render(data: dict[str, Any]) -> None:
        console.print(
            f"{data.get('kind')} {data.get('title') or data.get('id')}",
            highlight=False,
            markup=False,
        )
        inputs = data.get("inputs")
        if isinstance(inputs, list) and inputs:
            print_table(
                ["Input", "Type"],
                [
                    [
                        short(i.get("title") or i.get("source_id"), 50),
                        str(i.get("source_type") or i.get("type") or ""),
                    ]
                    for i in inputs
                    if isinstance(i, dict)
                ],
                title="Inputs",
            )
        if data.get("runs"):
            print_table(
                ["Run", "Type", "Status"],
                [
                    [str(r.get("id") or ""), str(r.get("type") or ""), str(r.get("status") or "")]
                    for r in data["runs"]
                ],
                title="Runs",
            )
        if data.get("versions"):
            console.print(f"{len(data['versions'])} versions", highlight=False)
        for actor in data.get("actors") or []:
            console.print(
                f"{actor.get('user_id')}: {', '.join(actor.get('roles') or [])}", markup=False
            )

    emit(result, json_out=json_out, render=render)


_TABLES = ("facts", "connections", "findings")


def _trace_row(
    card: str, *, table: str, row: str | None, value: str | None, project: str | None
) -> dict[str, Any]:
    """``GET /v1/trace/row/{card_id}``, resolving a card title to its id first."""
    from beakr_cli.commands.kb import _resolve_page

    if table not in _TABLES:
        raise typer.BadParameter(f"--table must be one of {', '.join(_TABLES)}")
    if not row:
        raise typer.BadParameter("--row is required: the row's index, or its Attribute/Relation")
    card_id = card if _is_uuid(card) else str((_resolve_page(card, project=project) or {})["id"])
    with get_sdk() as sdk:
        return sdk.trace.row(card_id, row=row, table=table, value=value)  # type: ignore[arg-type]


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
    except ValueError:
        return False
    return True


def _who(entry: dict[str, Any] | None) -> str:
    """One line for the change that wrote a row: revision, proposal, via, run."""
    if not entry:
        return "unknown"
    parts = [f"rev {entry.get('revision')}"]
    proposal = entry.get("proposal") or {}
    if proposal.get("id"):
        parts.append(f"proposal {proposal['id']} by {proposal.get('proposed_by') or '?'}")
        if proposal.get("reviewed_by"):
            parts.append(f"reviewed by {proposal['reviewed_by']}")
    elif (entry.get("commit") or {}).get("source_type"):
        parts.append(str(entry["commit"]["source_type"]))
    elif entry.get("written_by"):
        parts.append(f"written by {entry['written_by']}")
    via = entry.get("via") or {}
    labels = [f"{k}={via[k]}" for k in ("credential_kind", "client", "agent") if via.get(k)]
    if labels:
        parts.append("via " + " ".join(labels))
    run = entry.get("run") or {}
    if run.get("id"):
        parts.append(f"run {run.get('type')}:{run['id']}")
    return ", ".join(parts)


def _render_row(data: dict[str, Any]) -> None:
    row = data.get("row") or {}
    cells = row.get("cells") or {}
    console.print(
        f"{data.get('title')} / {data.get('table')} row {row.get('index')}: "
        + " | ".join(str(v) for v in cells.values() if v),
        highlight=False,
        markup=False,
    )
    console.print(f"Introduced: {_who(data.get('introduced_by'))}", highlight=False, markup=False)
    console.print(
        f"Last changed: {_who(data.get('last_changed_by'))}", highlight=False, markup=False
    )
    inputs = data.get("inputs") or []
    if inputs:
        print_table(
            ["Source", "source_ref", "Version read"],
            [
                [
                    short(str(i.get("name") or i.get("citation_key") or ""), 40),
                    str(i.get("source_ref") or ""),
                    _version_label(i.get("version") or {}),
                ]
                for i in inputs
                if isinstance(i, dict)
            ],
            title="Sources",
        )
    history = data.get("history") or []
    if history:
        print_table(
            ["Rev", "Change", "Value", "By"],
            [
                [
                    str(h.get("revision")),
                    str(h.get("change")),
                    short(" | ".join(str(v) for v in (h.get("cells") or {}).values() if v), 50),
                    short(_who(h), 70),
                ]
                for h in history
            ],
            title="History",
        )
    if not data.get("history_complete", True):
        console.print("History shows the most recent revisions only.", highlight=False)


def _version_label(version: dict[str, Any]) -> str:
    for key in ("unit_version_id", "artifact_version_id", "doc_sha256"):
        if version.get(key):
            read = str(version[key])
            current = version.get("current_unit_version_id")
            stale = key == "unit_version_id" and current and str(current) != read
            return f"{key}={read}" + (" (newer version exists)" if stale else "")
    return ""


def register(root: typer.Typer) -> None:
    root.command("usage")(usage)
    root.command("trace")(trace)
