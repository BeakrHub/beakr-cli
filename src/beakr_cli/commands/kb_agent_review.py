"""``beakr kb agent-review show|set``: whether a person must accept what agents write.

The policy (``GET``/``PUT /v1/knowledge/agent-review``, through the SDK's
``knowledge.agent_review``) has three fields; a project follows the
organization for every field it does not set. ``show`` prints the policy in
force and where each field comes from. ``set`` changes the organization's
policy, or with ``-P`` a project's own (``--follow-org`` drops it). Changing it
needs a signed-in person or a key holding ``proposals:accept``: any other key
is refused (``SessionRequiredError``), which exits 6 as the other work only a
person may do does.
"""

from __future__ import annotations

from typing import Any

import typer
from beakr_sdk import SessionRequiredError

from beakr_cli.commands.kb import app as kb_app
from beakr_cli.errors import EXIT_PERSON_REVIEW, error_object
from beakr_cli.output import emit, err_console, output_mode, print_json, print_table
from beakr_cli.project_ref import project_ref_callback
from beakr_cli.sdk import get_sdk

app = typer.Typer(help="Whether a person must accept what API keys and tokens write.")

#: The policy's fields, in reading order, with the option that sets each.
FIELDS: tuple[tuple[str, str, str], ...] = (
    (
        "agent_proposals_need_a_person",
        "--proposals",
        "A key or token may stage proposals but not accept them or commit Knowledge.",
    ),
    (
        "agent_uploads_need_a_person",
        "--uploads",
        "A file a key or token uploads waits (waiting_for_person) until a person approves it.",
    ),
    (
        "capture_auto_approve_allowed",
        "--capture-auto-approve",
        "With proposals held, a person's auto-approve still applies to capture suggestions.",
    ),
)

#: ``on``/``off`` set a field; ``unset`` drops it (a project's then follows the
#: organization, the organization's goes back to off).
_VALUES = {"on": True, "off": False, "unset": None}

#: What a refused change says: the server's session_required hint names the web
#: app only, but a key a person created with proposals:accept may change it too.
_REFUSED_HINT = (
    "Changing agent review needs a signed-in person or an API key a person created with the "
    "proposals:accept scope (BEAKR_API_KEY); this credential is neither. Change it in the "
    "Beakr web app, or ask a person to."
)

_project_opt = typer.Option(
    None,
    "--project",
    "-P",
    "--scope",
    "-s",
    help="Project id, slug or name (omit for the organization's policy).",
    callback=project_ref_callback,
)
_json_opt = typer.Option(False, "--json", "-j", help="Output raw JSON.")


def _value_opt(flag: str, help_text: str) -> Any:
    return typer.Option(None, flag, help=f"on, off or unset. {help_text}")


def _rows(settings: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per field: its value in force, where that comes from, and both layers."""
    effective = settings.get("effective") or {}
    org = settings.get("org") or {}
    override = settings.get("project_override") or {}
    rows = []
    for field, _flag, _help in FIELDS:
        own = override.get(field)
        rows.append(
            {
                "field": field,
                "effective": bool(effective.get(field)),
                "from": "project" if settings.get("project_id") and own is not None else "org",
                "org": bool(org.get(field)),
                "project": own,
            }
        )
    return rows


def _on(value: Any) -> str:
    return "-" if value is None else ("on" if value else "off")


def _show(settings: dict[str, Any], *, json_out: bool) -> None:
    def render(s: dict[str, Any]) -> None:
        if s.get("available") is False:
            err_console.print("Agent review cannot be changed in this organization.")
        where = f"project {s['project_id']}" if s.get("project_id") else "the organization"
        print_table(
            ["Field", "In force", "From", "Org", "Project"],
            [
                [
                    row["field"],
                    _on(row["effective"]),
                    row["from"],
                    _on(row["org"]),
                    _on(row["project"]),
                ]
                for row in _rows(s)
            ],
            title=f"Agent review for {where}",
        )

    def lines(s: dict[str, Any]) -> list[str]:
        return [
            f"{row['field']}\teffective={_on(row['effective'])}\tfrom={row['from']}"
            f"\torg={_on(row['org'])}\tproject={_on(row['project'])}"
            for row in _rows(s)
        ]

    emit(settings, json_out=json_out, render=render, lines=lines)


@app.command("show")
def show(project: str | None = _project_opt, json_out: bool = _json_opt) -> None:
    """The policy in force (for a project with -P), and whether org or project sets each field."""
    with get_sdk() as sdk:
        settings = sdk.knowledge.agent_review.get(project_id=project)
    _show(settings, json_out=json_out)


@app.command("set")
def set_policy(
    project: str | None = _project_opt,
    proposals: str | None = _value_opt(FIELDS[0][1], FIELDS[0][2]),
    uploads: str | None = _value_opt(FIELDS[1][1], FIELDS[1][2]),
    capture: str | None = _value_opt(FIELDS[2][1], FIELDS[2][2]),
    follow_org: bool = typer.Option(
        False, "--follow-org", help="With -P: drop the project's own policy; it follows the org."
    ),
    json_out: bool = _json_opt,
) -> None:
    """Change the organization's policy, or a project's own with -P.

    Only the fields given change; `unset` drops one (a project's then follows
    the organization). Needs a signed-in person or a key holding
    proposals:accept (exit 6 otherwise).
    """
    given = {
        field: value
        for (field, _flag, _help), value in zip(FIELDS, (proposals, uploads, capture), strict=True)
        if value is not None
    }
    if follow_org and not project:
        _usage("--follow-org drops a project's own policy: give the project with -P.")
    if follow_org and given:
        _usage("--follow-org drops every field the project sets; give it without field options.")
    if not follow_org and not given:
        _usage("Give a field to change: --proposals, --uploads or --capture-auto-approve.")
    changes: dict[str, bool | None] = {}
    for field, value in given.items():
        if value not in _VALUES:
            _usage(f"{dict((f, o) for f, o, _ in FIELDS)[field]} takes on, off or unset.")
        changes[field] = _VALUES[value]
    try:
        # Each answers the settings as `show` reads them: what is in force, from where.
        with get_sdk() as sdk:
            review = sdk.knowledge.agent_review
            if follow_org:
                settings = review.follow_org(str(project))
            elif project:
                settings = review.set_project(str(project), **changes)
            else:
                settings = review.set(**changes)
    except SessionRequiredError as refused:
        _refused(refused, json_out=json_out)
    _show(settings, json_out=json_out)


def _refused(exc: SessionRequiredError, *, json_out: bool) -> None:
    """A credential that may not change the policy: exit 6, the work needs a person."""
    if output_mode(json_out) == "json":
        body = error_object(exc, EXIT_PERSON_REVIEW, ("kb", "agent-review", "set"))
        body["error"]["fix"] = _REFUSED_HINT
        print_json(body)
    err_console.print(f"Error: {exc.message}\n{_REFUSED_HINT}", markup=False, highlight=False)
    raise typer.Exit(EXIT_PERSON_REVIEW) from exc


def _usage(message: str) -> None:
    err_console.print(message, markup=False)
    raise typer.Exit(2)


def register(root: typer.Typer) -> None:
    """Attach ``kb agent-review`` (the root is unused)."""
    kb_app.add_typer(app, name="agent-review")
