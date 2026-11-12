"""Project isolation administration: ``beakr org isolation``, ``beakr org read-trail``,
``beakr projects guard``.

The settings (``GET``/``PUT /v1/organizations/{org_id}/project-isolation``,
``/v1/organizations/{org_id}/read-trail`` and
``/v1/projects/{project_id}/cross-project-guard``) confine what agents write, so
these commands are a person's alone:

- every request carries the person's own credential and no ``X-Beakr-Session``
  (:func:`scope_session.person_credential` with ``unjoined``), never a session
  token, so a change is joined to no agent session;
- none of them is an MCP tool (``tests/test_isolation_admin.py`` checks that no
  MCP tool can reach these routes): an agent must never loosen its own guard;
- a change that loosens a rule (a guard less strict than the one in force,
  ``agent_writes_require_session`` or ``auto_lock_counts_search_results`` off,
  a shorter read trail, which deletes older reads) asks first; ``--yes`` skips it.

Before a ``set`` sends anything it reads the settings: a part the installation
has not turned on is refused with the flag to turn on (exit 4), and a caller the
API says may not change it (``can_change``) is refused (exit 3). The API checks
both again; only the fields given are sent.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

import typer
from beakr_sdk.errors import BeakrError, NotFoundError, PermissionDeniedError

from beakr_cli import scope_session
from beakr_cli.commands.org import app as org_app
from beakr_cli.commands.projects import app as projects_app
from beakr_cli.output import emit, err_console, print_table
from beakr_cli.prompts import confirm_or_require_yes
from beakr_cli.sdk import get_sdk

isolation_app = typer.Typer(
    help="Project isolation: agent session mode, sessions for agent writes, the "
    "cross-project guard."
)
read_trail_app = typer.Typer(help="How long an agent session's reads are kept.")
guard_app = typer.Typer(
    help="A project's cross-project guard: allow, hold or refuse writes drawn from other "
    "projects, or follow the organization's default."
)

_json_opt = typer.Option(False, "--json", "-j", help="Output raw JSON.")
_org_opt = typer.Option(None, "--org", help="Organization id (default: the org you act in).")
_yes_opt = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt.")


class SessionMode(str, Enum):
    picker = "picker"
    auto_lock = "auto_lock"


class Guard(str, Enum):
    allow = "allow"
    hold = "hold"
    refuse = "refuse"


class ProjectGuard(str, Enum):
    allow = "allow"
    hold = "hold"
    refuse = "refuse"
    default = "default"


#: Each organization setting, the feature flag that must be on to change it
#: (``beakr/services/project_isolation.py`` ``SETTING_FLAGS``), and its label.
SETTINGS: tuple[tuple[str, str, str], ...] = (
    ("session_mode", "session_auto_lock", "Session mode"),
    ("auto_lock_counts_search_results", "session_auto_lock", "Search results count as read"),
    ("agent_writes_require_session", "agent_writes_require_session", "Agent writes need a session"),
    ("cross_project_guard", "cross_project_guard", "Cross-project guard"),
)
#: The flag behind agent sessions themselves, and behind the read trail.
AGENT_SESSIONS_FLAG = "server_agent_sessions"
CROSS_PROJECT_GUARD_FLAG = "cross_project_guard"
READ_TRAIL_FLAG = "session_read_trail"

#: Strictness of each guard: moving down this order loosens it.
_GUARD_RANK = {"allow": 0, "hold": 1, "refuse": 2}

FEATURE_NOT_ENABLED = "feature_not_enabled"
#: The settings say this caller may not change them (``can_change: false``).
CHANGE_NOT_ALLOWED = "change_not_allowed"


def _person_sdk() -> Any:
    """The person's own client: no session token, no ``X-Beakr-Session``."""
    return get_sdk(scoped=False)


def _org_id(org: str | None) -> str:
    if org:
        return org
    from beakr_cli.project_binding import credential_org_id

    found = credential_org_id()
    if not found:
        raise BeakrError(
            "Could not tell which organization this credential acts in.",
            code="org_unknown",
            fix="Pass --org ORG_ID.",
        )
    return found


def _isolation_path(org_id: str) -> str:
    return scope_session.isolation_path(org_id)


def _read_trail_path(org_id: str) -> str:
    return f"/v1/organizations/{org_id}/read-trail"


def _guard_path(project_id: str) -> str:
    return f"/v1/projects/{project_id}/cross-project-guard"


def _operator_fix(flags: list[str]) -> str:
    return f"Ask your operator to turn on {', '.join(sorted(set(flags)))} on the Features page."


def not_enabled(flags: list[str]) -> NotFoundError:
    """A part of project isolation the installation has not turned on (exit 4)."""
    return NotFoundError(
        f"Not turned on for this organization: {', '.join(sorted(set(flags)))}. "
        "Nothing was changed.",
        status=404,
        code=FEATURE_NOT_ENABLED,
        fix=_operator_fix(flags),
    )


def _not_turned_on_note(flags: list[str]) -> None:
    """On stderr, beside a ``show``: the parts not turned on, and who turns them on."""
    if flags:
        err_console.print(
            f"Not turned on: {', '.join(sorted(set(flags)))}. {_operator_fix(flags)}",
            markup=False,
            highlight=False,
            soft_wrap=True,
        )


def _cannot_change(what: str, who: str) -> PermissionDeniedError:
    return PermissionDeniedError(
        f"You can't change {what}: it needs {who}, acting as a person. Nothing was changed.",
        status=403,
        code=CHANGE_NOT_ALLOWED,
        fix="Sign in to the Beakr web app, or use an API key you created with the "
        "proposals:accept scope (BEAKR_API_KEY); otherwise ask someone who can.",
    )


def _explained(exc: BeakrError, flags: list[str]) -> BeakrError:
    """The API's ``feature_not_enabled`` with the fix that works on a self-hosted install."""
    if isinstance(exc, NotFoundError) and exc.code == FEATURE_NOT_ENABLED:
        return not_enabled(flags)
    return exc


def _confirm_loosening(changes: list[str], yes: bool) -> None:
    if changes:
        confirm_or_require_yes(f"Loosen project isolation: {'; '.join(changes)}?", yes)


def _on(value: Any) -> str:
    return "on" if value else "off"


def _yes_no(value: Any) -> str:
    return "yes" if value else "no"


# -- org isolation -------------------------------------------------------------


def _shown(name: str, value: Any) -> str:
    return str(value) if name in {"session_mode", "cross_project_guard"} else _on(value)


def _off_flags(settings: dict[str, Any]) -> list[str]:
    available = settings.get("available") or {}
    flags = [AGENT_SESSIONS_FLAG, *(flag for _, flag, _ in SETTINGS)]
    return sorted({flag for flag in flags if not available.get(flag)})


def _isolation_notes(settings: dict[str, Any]) -> None:
    _not_turned_on_note(_off_flags(settings))


def _show_isolation(settings: dict[str, Any], *, json_out: bool) -> None:
    available = settings.get("available") or {}

    def render(s: dict[str, Any]) -> None:
        _isolation_notes(s)
        print_table(
            ["Setting", "Value", "Turned on"],
            [
                [label, _shown(name, s.get(name)), _yes_no(available.get(flag))]
                for name, flag, label in SETTINGS
            ],
            title="Project isolation",
        )
        err_console.print(
            f"Agent sessions: {_on(available.get(AGENT_SESSIONS_FLAG))}. "
            f"You can change these: {_yes_no(s.get('can_change'))}.",
            markup=False,
            highlight=False,
            soft_wrap=True,
        )

    def lines(s: dict[str, Any]) -> list[str]:
        _isolation_notes(s)
        return [
            f"{name}\t{_shown(name, s.get(name))}\tturned_on={_yes_no(available.get(flag))}"
            for name, flag, _ in SETTINGS
        ] + [f"can_change\t{_yes_no(s.get('can_change'))}"]

    emit(settings, json_out=json_out, render=render, lines=lines)


@isolation_app.command("show")
def show_isolation(org: str | None = _org_opt, json_out: bool = _json_opt) -> None:
    """The organization's project isolation settings, and which parts are turned on."""
    with scope_session.person_credential(unjoined=True), _person_sdk() as sdk:
        settings = sdk.request("GET", _isolation_path(_org_id(org)))
    _show_isolation(settings, json_out=json_out)


@isolation_app.command("set")
def set_isolation(
    session_mode: SessionMode | None = typer.Option(
        None,
        "--session-mode",
        help="auto_lock: the first project an agent session touches becomes its project. "
        "picker: the person chooses each session's projects.",
    ),
    require_session: bool | None = typer.Option(
        None,
        "--require-session/--no-require-session",
        help="Refuse Knowledge and file writes from a person's key or token outside a session.",
    ),
    guard: Guard | None = typer.Option(
        None,
        "--guard",
        help="The organization's default for writes drawn from another project: allow, hold "
        "(an owner or admin of the project must accept them) or refuse.",
    ),
    count_search_results: bool | None = typer.Option(
        None,
        "--count-search-results/--no-count-search-results",
        help="In auto_lock, whether the projects an unscoped search's results come from "
        "count as read.",
    ),
    org: str | None = _org_opt,
    yes: bool = _yes_opt,
    json_out: bool = _json_opt,
) -> None:
    """Change project isolation; only the options given are sent.

    Needs an organization admin acting as a person (the web app, or a key holding
    proposals:accept). A part not turned on exits 4; a caller who may not change
    it, 3. Loosening a rule (a less strict guard, --no-require-session,
    --no-count-search-results) asks first; --yes skips the question.
    """
    scope_session.refuse_inside_conversation("beakr org isolation set", "Change project isolation")
    changes: dict[str, Any] = {}
    if session_mode is not None:
        changes["session_mode"] = session_mode.value
    if count_search_results is not None:
        changes["auto_lock_counts_search_results"] = count_search_results
    if require_session is not None:
        changes["agent_writes_require_session"] = require_session
    if guard is not None:
        changes["cross_project_guard"] = guard.value
    if not changes:
        _usage(
            "Give a setting to change: --session-mode, --require-session/--no-require-session, "
            "--guard or --count-search-results/--no-count-search-results."
        )
    flags = [flag for name, flag, _ in SETTINGS if name in changes]
    with scope_session.person_credential(unjoined=True), _person_sdk() as sdk:
        path = _isolation_path(_org_id(org))
        current = sdk.request("GET", path)
        available = current.get("available") or {}
        off = [flag for flag in flags if not available.get(flag)]
        if off:
            raise not_enabled(off)
        if not current.get("can_change"):
            raise _cannot_change("project isolation settings", "an organization admin")
        _confirm_loosening(_loosened_isolation(current, changes), yes)
        try:
            updated = sdk.request("PUT", path, json=changes)
        except BeakrError as exc:
            raise _explained(exc, flags) from exc
    _show_isolation(updated, json_out=json_out)


def _loosened_isolation(current: dict[str, Any], changes: dict[str, Any]) -> list[str]:
    """What in ``changes`` loosens a rule now in force, said for the question."""
    loosened = []
    guard = changes.get("cross_project_guard")
    now = str(current.get("cross_project_guard") or "allow")
    if guard is not None and _GUARD_RANK[guard] < _GUARD_RANK.get(now, 0):
        loosened.append(f"cross-project guard {now} -> {guard}")
    for name, label in (
        ("agent_writes_require_session", "agent writes need a session"),
        ("auto_lock_counts_search_results", "search results count as read"),
    ):
        if changes.get(name) is False and current.get(name):
            loosened.append(f"{label} on -> off")
    return loosened


# -- org read-trail --------------------------------------------------------------


def _show_read_trail(settings: dict[str, Any], *, json_out: bool) -> None:
    def note(s: dict[str, Any]) -> None:
        _not_turned_on_note([] if s.get("available") else [READ_TRAIL_FLAG])

    def render(s: dict[str, Any]) -> None:
        note(s)
        print_table(
            ["Setting", "Value"],
            [
                ["Reads kept for", f"{s.get('read_trail_retention_days')} days"],
                ["Turned on", _yes_no(s.get("available"))],
                ["You can change it", _yes_no(s.get("can_change"))],
            ],
            title="Read trail",
        )

    def lines(s: dict[str, Any]) -> list[str]:
        note(s)
        return [
            f"read_trail_retention_days\t{s.get('read_trail_retention_days')}",
            f"turned_on\t{_yes_no(s.get('available'))}",
            f"can_change\t{_yes_no(s.get('can_change'))}",
        ]

    emit(settings, json_out=json_out, render=render, lines=lines)


@read_trail_app.command("show")
def show_read_trail(org: str | None = _org_opt, json_out: bool = _json_opt) -> None:
    """How many days an agent session's reads are kept."""
    with scope_session.person_credential(unjoined=True), _person_sdk() as sdk:
        settings = sdk.request("GET", _read_trail_path(_org_id(org)))
    _show_read_trail(settings, json_out=json_out)


@read_trail_app.command("set")
def set_read_trail(
    days: int = typer.Option(
        ..., "--days", min=1, max=3650, help="Days to keep a session's reads (1 to 3650)."
    ),
    org: str | None = _org_opt,
    yes: bool = _yes_opt,
    json_out: bool = _json_opt,
) -> None:
    """Change how long an agent session's reads are kept.

    Needs an organization admin acting as a person. Reads older than the new
    period are deleted within the hour, so a shorter period asks first; --yes
    skips the question.
    """
    scope_session.refuse_inside_conversation("beakr org read-trail set", "Change the read trail")
    with scope_session.person_credential(unjoined=True), _person_sdk() as sdk:
        path = _read_trail_path(_org_id(org))
        current = sdk.request("GET", path)
        if not current.get("available"):
            raise not_enabled([READ_TRAIL_FLAG])
        if not current.get("can_change"):
            raise _cannot_change("read trail retention", "an organization admin")
        kept = current.get("read_trail_retention_days")
        if isinstance(kept, int) and days < kept:
            confirm_or_require_yes(
                f"Keep reads for {days} days instead of {kept}? Reads older than {days} days "
                "are deleted within the hour.",
                yes,
            )
        try:
            updated = sdk.request("PUT", path, json={"read_trail_retention_days": days})
        except BeakrError as exc:
            raise _explained(exc, [READ_TRAIL_FLAG]) from exc
    _show_read_trail(updated, json_out=json_out)


# -- projects guard ----------------------------------------------------------------


def _project_id(ref: str) -> str:
    """The project's id, resolved with the person's own credential (inside the caller's
    :func:`scope_session.person_credential`)."""
    from beakr_cli.project_ref import resolve_project_ref

    return resolve_project_ref(ref, param_hint="PROJECT")


def _show_guard(guard: dict[str, Any], *, json_out: bool) -> None:
    def note(g: dict[str, Any]) -> None:
        _not_turned_on_note([] if g.get("available") else [CROSS_PROJECT_GUARD_FLAG])

    def own(g: dict[str, Any]) -> str:
        return str(g.get("override") or "default (follows the organization)")

    def render(g: dict[str, Any]) -> None:
        note(g)
        print_table(
            ["Setting", "Value"],
            [
                ["Project", f"{g.get('project_name') or ''} ({g.get('project_id')})"],
                ["In force", str(g.get("effective"))],
                ["Project's own", own(g)],
                ["Organization default", str(g.get("org_default"))],
                ["Turned on", _yes_no(g.get("available"))],
                ["You can change it", _yes_no(g.get("can_change"))],
            ],
            title="Cross-project guard",
        )

    def lines(g: dict[str, Any]) -> list[str]:
        note(g)
        return [
            f"project\t{g.get('project_id')}\t{g.get('project_name') or ''}",
            f"effective\t{g.get('effective')}",
            f"override\t{g.get('override') or 'default'}",
            f"org_default\t{g.get('org_default')}",
            f"turned_on\t{_yes_no(g.get('available'))}",
            f"can_change\t{_yes_no(g.get('can_change'))}",
        ]

    emit(guard, json_out=json_out, render=render, lines=lines)


@guard_app.command("show")
def show_guard(
    project: str = typer.Argument(..., help="Project id, slug or name."),
    json_out: bool = _json_opt,
) -> None:
    """The guard in force for a project: its own, else the organization's."""
    with scope_session.person_credential(unjoined=True):
        project_id = _project_id(project)
        with _person_sdk() as sdk:
            guard = sdk.request("GET", _guard_path(project_id))
    _show_guard(guard, json_out=json_out)


@guard_app.command("set")
def set_guard(
    project: str = typer.Argument(..., help="Project id, slug or name."),
    mode: ProjectGuard = typer.Argument(
        ..., help="allow, hold or refuse; default follows the organization's guard."
    ),
    yes: bool = _yes_opt,
    json_out: bool = _json_opt,
) -> None:
    """Set a project's own cross-project guard, or `default` to follow the organization.

    Needs an owner or admin of the project acting as a person. Where the guard is
    not turned on, exits 4; a caller who may not change it, 3. A guard less strict
    than the one in force asks first; --yes skips the question.
    """
    scope_session.refuse_inside_conversation("beakr projects guard set", "Change a project's guard")
    override = None if mode is ProjectGuard.default else mode.value
    with scope_session.person_credential(unjoined=True):
        project_id = _project_id(project)
        with _person_sdk() as sdk:
            path = _guard_path(project_id)
            current = sdk.request("GET", path)
            if not current.get("available"):
                raise not_enabled([CROSS_PROJECT_GUARD_FLAG])
            if not current.get("can_change"):
                raise _cannot_change(
                    "this project's cross-project guard", "an owner or admin of the project"
                )
            now = str(current.get("effective") or "allow")
            after = override or str(current.get("org_default") or "allow")
            if _GUARD_RANK.get(after, 0) < _GUARD_RANK.get(now, 0):
                name = current.get("project_name") or project_id
                _confirm_loosening([f"{name}'s cross-project guard {now} -> {after}"], yes)
            try:
                updated = sdk.request("PUT", path, json={"override": override})
            except BeakrError as exc:
                raise _explained(exc, [CROSS_PROJECT_GUARD_FLAG]) from exc
    _show_guard(updated, json_out=json_out)


def _usage(message: str) -> None:
    err_console.print(message, markup=False)
    raise typer.Exit(2)


def register(root: typer.Typer) -> None:
    """Attach ``org isolation``, ``org read-trail`` and ``projects guard`` (root unused)."""
    org_app.add_typer(isolation_app, name="isolation")
    org_app.add_typer(read_trail_app, name="read-trail")
    projects_app.add_typer(guard_app, name="guard")
