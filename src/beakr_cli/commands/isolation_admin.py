"""Project isolation administration: ``beakr org isolation``, ``beakr org read-trail``,
``beakr projects boundary`` (and, for one release, ``beakr projects guard``).

The settings (``GET``/``PUT /v1/organizations/{org_id}/project-isolation``,
``/v1/organizations/{org_id}/read-trail``, a project's ``boundary`` on
``PATCH /v1/projects/{project_id}`` and the legacy
``/v1/projects/{project_id}/cross-project-guard``) confine what agents write, so
these commands are a person's alone:

- every request carries the person's own credential and no ``X-Beakr-Session``
  (:func:`scope_session.person_credential` with ``unjoined``), never a session
  token, so a change is joined to no agent session;
- none of them is an MCP tool (``tests/test_isolation_admin.py`` checks that no
  MCP tool can reach these routes): an agent must never loosen its own boundary;
- a change that loosens a rule (a project boundary or the organization's default
  or minimum set lower, sessions no longer made to pick, ``agent_writes_require_session``
  off, a shorter read trail, which deletes older reads) asks first; ``--yes`` skips it.

Project boundaries replaced the organization-wide auto-lock and the per-project
cross-project guard. Each project has a ``content_out`` (``allowed`` or ``locked``:
once a session touches it, the session writes only to it) and a ``content_in``
(``allowed``, ``owner_approves`` or ``blocked``: what happens to a write that brings
in another project's content). The old options are kept for one release with a
deprecation message: ``--session-mode picker|auto_lock``, ``--guard`` and
``--count-search-results`` of ``org isolation set``, and ``projects guard``.

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
from beakr_cli.output import console, emit, err_console, print_table
from beakr_cli.prompts import confirm_or_require_yes
from beakr_cli.sdk import get_sdk

isolation_app = typer.Typer(
    help="Project isolation: how agent sessions start, the boundary new projects start with "
    "and the minimum every project keeps, sessions for agent writes."
)
read_trail_app = typer.Typer(help="How long an agent session's reads are kept.")
boundary_app = typer.Typer(
    help="A project's boundary: whether touching it locks a session's writes to it "
    "(content out), and what happens to writes that bring in other projects' content "
    "(content in)."
)
guard_app = typer.Typer(
    help="Deprecated: a project's cross-project guard is now its boundary's content in "
    "(`beakr projects boundary`). Kept for one release."
)

_json_opt = typer.Option(False, "--json", "-j", help="Output raw JSON.")
_org_opt = typer.Option(None, "--org", help="Organization id (default: the org you act in).")
_yes_opt = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt.")


class SessionMode(str, Enum):
    automatic = "automatic"
    must_pick = "must_pick"
    # Deprecated (one release): what they were before project boundaries.
    picker = "picker"
    auto_lock = "auto_lock"


class ContentOut(str, Enum):
    allowed = "allowed"
    locked = "locked"


class ContentIn(str, Enum):
    allowed = "allowed"
    owner_approves = "owner_approves"
    blocked = "blocked"


class Preset(str, Enum):
    internal = "internal"
    siloed = "siloed"
    leadership = "leadership"


class Guard(str, Enum):
    allow = "allow"
    hold = "hold"
    refuse = "refuse"


class ProjectGuard(str, Enum):
    allow = "allow"
    hold = "hold"
    refuse = "refuse"
    default = "default"


#: Each organization setting, the feature (``available`` key) that must be on to
#: change it, and its label.
SETTINGS: tuple[tuple[str, str, str], ...] = (
    ("session_mode", "project_boundaries", "Session mode"),
    ("default_boundary", "project_boundaries", "New projects start"),
    ("minimum_boundary", "project_boundaries", "Minimum boundary"),
    ("agent_writes_require_session", "agent_writes_require_session", "Agent writes need a session"),
)
#: The flag behind agent sessions themselves, and behind the read trail.
AGENT_SESSIONS_FLAG = "server_agent_sessions"
#: Project boundaries are behind the flag key ``cross_project_guard``, titled
#: "Project boundaries" on the Features page; the API reports it as ``project_boundaries``.
BOUNDARIES_FEATURE = "project_boundaries"
CROSS_PROJECT_GUARD_FLAG = "cross_project_guard"
READ_TRAIL_FLAG = "session_read_trail"
#: How a feature is named to the operator who turns it on.
_FLAG_NAMES = {
    BOUNDARIES_FEATURE: f"Project boundaries ({CROSS_PROJECT_GUARD_FLAG})",
    CROSS_PROJECT_GUARD_FLAG: f"Project boundaries ({CROSS_PROJECT_GUARD_FLAG})",
}

#: Strictness of each value: moving down these orders loosens a boundary.
_OUT_RANK = {"allowed": 0, "locked": 1}
_IN_RANK = {"allowed": 0, "owner_approves": 1, "blocked": 2}
_GUARD_RANK = {"allow": 0, "hold": 1, "refuse": 2}
#: The legacy guard's value as a boundary's content in.
_GUARD_TO_IN = {"allow": "allowed", "hold": "owner_approves", "refuse": "blocked"}
#: The presets' pairs (the API's ``presets`` list says the same).
PRESETS = {
    "internal": {"content_out": "allowed", "content_in": "allowed"},
    "siloed": {"content_out": "locked", "content_in": "owner_approves"},
    "leadership": {"content_out": "allowed", "content_in": "owner_approves"},
}
_PRESET_TITLES = {
    "internal": "Internal",
    "siloed": "Partner project (siloed)",
    "leadership": "Leadership",
    "custom": "Custom",
}

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


def _flag_names(flags: list[str]) -> str:
    return ", ".join(_FLAG_NAMES.get(flag, flag) for flag in sorted(set(flags)))


def _operator_fix(flags: list[str]) -> str:
    return f"Ask your operator to turn on {_flag_names(flags)} on the Features page."


def not_enabled(flags: list[str]) -> NotFoundError:
    """A part of project isolation the installation has not turned on (exit 4)."""
    return NotFoundError(
        f"Not turned on for this organization: {_flag_names(flags)}. Nothing was changed.",
        status=404,
        code=FEATURE_NOT_ENABLED,
        fix=_operator_fix(flags),
    )


def _not_turned_on_note(flags: list[str]) -> None:
    """On stderr, beside a ``show``: the parts not turned on, and who turns them on."""
    if flags:
        err_console.print(
            f"Not turned on: {_flag_names(flags)}. {_operator_fix(flags)}",
            markup=False,
            highlight=False,
            soft_wrap=True,
        )


def _deprecated(message: str) -> None:
    """A deprecated option or command, said on stderr; it still works for one release."""
    err_console.print(f"Deprecated: {message}", markup=False, highlight=False, soft_wrap=True)


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


def boundary_words(boundary: Any) -> str:
    """``content out locked, content in owner_approves (Partner project (siloed))``."""
    if not isinstance(boundary, dict) or not boundary:
        return "none"
    parts = [
        f"content {key.removeprefix('content_')} {boundary[key]}"
        for key in ("content_out", "content_in")
        if boundary.get(key)
    ]
    preset = next(
        (key for key, pair in PRESETS.items() if pair == {k: boundary.get(k) for k in pair}),
        None,
    )
    return ", ".join(parts) + (f" ({_PRESET_TITLES[preset]})" if preset else "")


def _lower(before: Any, after: Any) -> list[str]:
    """The keys ``after`` sets lower than ``before`` (a missing key is the lowest)."""
    before = before if isinstance(before, dict) else {}
    after = after if isinstance(after, dict) else {}
    lowered = []
    for key, rank in (("content_out", _OUT_RANK), ("content_in", _IN_RANK)):
        if rank.get(str(after.get(key) or "allowed"), 0) < rank.get(
            str(before.get(key) or "allowed"), 0
        ):
            lowered.append(
                f"{key.replace('_', ' ')} {before.get(key)} -> {after.get(key) or 'allowed'}"
            )
    return lowered


# -- org isolation -------------------------------------------------------------


def _shown(name: str, value: Any) -> str:
    if name == "session_mode":
        return str(value)
    if name in {"default_boundary", "minimum_boundary"}:
        return boundary_words(value)
    return _on(value)


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


def _boundary_given(
    preset: Preset | None, out: ContentOut | None, in_: ContentIn | None, *, what: str
) -> dict[str, str] | None:
    """A boundary from a preset or from its two values (not both); None when none given."""
    if preset is not None and (out is not None or in_ is not None):
        _usage(f"Give {what} as a preset or as its content out/in values, not both.")
    if preset is not None:
        return dict(PRESETS[preset.value])
    given = {}
    if out is not None:
        given["content_out"] = out.value
    if in_ is not None:
        given["content_in"] = in_.value
    return given or None


@isolation_app.command("set")
def set_isolation(
    session_mode: SessionMode | None = typer.Option(
        None,
        "--session-mode",
        help="automatic: a session starts open and Beakr tracks what it touches. must_pick: "
        "every agent session chooses its projects (or read everything) before it starts. "
        "(picker and auto_lock are deprecated.)",
    ),
    default_preset: Preset | None = typer.Option(
        None,
        "--default-boundary",
        help="The boundary new projects start with, as a preset: internal, siloed (partner "
        "project) or leadership.",
    ),
    default_out: ContentOut | None = typer.Option(
        None, "--default-out", help="New projects' content out: allowed or locked."
    ),
    default_in: ContentIn | None = typer.Option(
        None,
        "--default-in",
        help="New projects' content in: allowed, owner_approves or blocked.",
    ),
    minimum_out: ContentOut | None = typer.Option(
        None,
        "--minimum-out",
        help="The lowest content out any project may have; locked keeps every project "
        "locked (the old organization-wide auto-lock). allowed drops this floor.",
    ),
    minimum_in: ContentIn | None = typer.Option(
        None,
        "--minimum-in",
        help="The lowest content in any project may have; allowed drops this floor.",
    ),
    no_minimum: bool = typer.Option(
        False, "--no-minimum", help="Drop the minimum boundary altogether."
    ),
    require_session: bool | None = typer.Option(
        None,
        "--require-session/--no-require-session",
        help="Refuse Knowledge and file writes from a person's key or token outside a session.",
    ),
    guard: Guard | None = typer.Option(
        None,
        "--guard",
        help="Deprecated: use --default-in. allow, hold or refuse become allowed, "
        "owner_approves or blocked for new projects.",
    ),
    count_search_results: bool | None = typer.Option(
        None,
        "--count-search-results/--no-count-search-results",
        help="Retired: a search that names no project now skips locked projects the "
        "session has not touched, so it never locks a session. Ignored.",
    ),
    org: str | None = _org_opt,
    yes: bool = _yes_opt,
    json_out: bool = _json_opt,
) -> None:
    """Change project isolation; only the options given are sent.

    Needs an organization admin acting as a person (the web app, or a key holding
    proposals:accept). A part not turned on exits 4; a caller who may not change
    it, 3. Loosening a rule (automatic instead of must_pick, a lower default or
    minimum boundary, --no-require-session) asks first; --yes skips the question.
    """
    scope_session.refuse_inside_conversation("beakr org isolation set", "Change project isolation")
    changes: dict[str, Any] = {}
    if count_search_results is not None:
        _deprecated(
            "--count-search-results was retired by project boundaries: results of a search "
            "that names no project always count as read, and locked projects the session has "
            "not touched are left out of it, so it can never lock a session. Nothing is sent "
            "for it."
        )
    if session_mode is SessionMode.picker:
        _deprecated("--session-mode picker is now must_pick; sent as must_pick.")
        changes["session_mode"] = scope_session.MUST_PICK
    elif session_mode is SessionMode.auto_lock:
        _deprecated(
            "--session-mode auto_lock is now an automatic session mode with every project "
            "locked (--session-mode automatic --minimum-out locked); sent as that."
        )
        changes["session_mode"] = scope_session.AUTOMATIC
        minimum_out = minimum_out or ContentOut.locked
    elif session_mode is not None:
        changes["session_mode"] = session_mode.value
    default = _boundary_given(default_preset, default_out, default_in, what="the default boundary")
    if guard is not None:
        _deprecated(
            f"--guard is now the content in new projects start with (--default-in); each "
            f"existing project keeps its own. Sent as --default-in {_GUARD_TO_IN[guard.value]}."
        )
        if default is not None and "content_in" in default:
            _usage("Give --guard or --default-in, not both.")
        default = {**(default or {}), "content_in": _GUARD_TO_IN[guard.value]}
    if no_minimum and (minimum_out is not None or minimum_in is not None):
        _usage("Give --no-minimum or --minimum-out/--minimum-in, not both.")
    if require_session is not None:
        changes["agent_writes_require_session"] = require_session
    if not (changes or default or no_minimum or minimum_out or minimum_in):
        if count_search_results is not None:
            return
        _usage(
            "Give a setting to change: --session-mode, --default-boundary (or --default-out/"
            "--default-in), --minimum-out/--minimum-in or --no-minimum, or "
            "--require-session/--no-require-session."
        )
    with scope_session.person_credential(unjoined=True), _person_sdk() as sdk:
        path = _isolation_path(_org_id(org))
        current = sdk.request("GET", path)
        if default is not None:
            # A key not given keeps its value.
            changes["default_boundary"] = {**(current.get("default_boundary") or {}), **default}
        if no_minimum:
            changes["minimum_boundary"] = None
        elif minimum_out is not None or minimum_in is not None:
            floor = dict(current.get("minimum_boundary") or {})
            for key, value in (("content_out", minimum_out), ("content_in", minimum_in)):
                if value is None:
                    continue
                if value.value == "allowed":
                    floor.pop(key, None)  # the lowest value is no floor
                else:
                    floor[key] = value.value
            changes["minimum_boundary"] = floor or None
        available = current.get("available") or {}
        flags = [flag for name, flag, _ in SETTINGS if name in changes]
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
    if (
        changes.get("session_mode") == scope_session.AUTOMATIC
        and current.get("session_mode") == scope_session.MUST_PICK
    ):
        loosened.append("session mode must_pick -> automatic")
    if "default_boundary" in changes:
        loosened.extend(
            f"new projects' {change}"
            for change in _lower(current.get("default_boundary"), changes["default_boundary"])
        )
    if "minimum_boundary" in changes:
        loosened.extend(
            f"minimum {change}"
            for change in _lower(current.get("minimum_boundary"), changes["minimum_boundary"])
        )
    if changes.get("agent_writes_require_session") is False and current.get(
        "agent_writes_require_session"
    ):
        loosened.append("agent writes need a session on -> off")
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
    """Deprecated: `beakr projects boundary show`. The guard in force for a project."""
    _deprecated(_GUARD_DEPRECATED)
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
    """Deprecated: `beakr projects boundary set --in`. Set a project's content in as a guard.

    allow, hold and refuse set the boundary's content in to allowed, owner_approves
    and blocked; default sets it to allowed. Needs an owner or admin of the project
    acting as a person. Where project boundaries are not turned on, exits 4; a
    caller who may not change it, 3. A guard less strict than the one in force asks
    first; --yes skips the question.
    """
    _deprecated(_GUARD_DEPRECATED)
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


_GUARD_DEPRECATED = (
    "`beakr projects guard` is now a project's boundary, content in: use `beakr projects "
    "boundary show` and `beakr projects boundary set PROJECT --in allowed|owner_approves|"
    "blocked`. It keeps working for one release."
)


# -- projects boundary -------------------------------------------------------------


def _project_path(project_id: str) -> str:
    return f"/v1/projects/{project_id}"


def _boundary_rows(project: dict[str, Any]) -> list[tuple[str, str]]:
    boundary = project.get("boundary") or {}
    stored = boundary.get("stored") or {}
    rows = [
        ("Project", f"{project.get('name') or ''} ({project.get('id')})"),
        ("In force", boundary_words(boundary)),
        ("Content out", str(boundary.get("content_out") or "")),
        ("Content in", str(boundary.get("content_in") or "")),
        ("Project's own", boundary_words(stored)),
        ("Raised to the minimum", _yes_no(boundary.get("raised_by_minimum"))),
        ("You can change it", _yes_no(boundary.get("can_change"))),
    ]
    return rows


def _show_boundary(project: dict[str, Any], *, json_out: bool) -> None:
    def note(p: dict[str, Any]) -> None:
        _not_turned_on_note([] if isinstance(p.get("boundary"), dict) else [BOUNDARIES_FEATURE])

    def render(p: dict[str, Any]) -> None:
        note(p)
        if not isinstance(p.get("boundary"), dict):
            return
        print_table(
            ["Setting", "Value"], [list(row) for row in _boundary_rows(p)], title="Boundary"
        )
        console.print(str(p["boundary"].get("summary") or ""), markup=False, soft_wrap=True)

    def lines(p: dict[str, Any]) -> list[str]:
        note(p)
        boundary = p.get("boundary")
        if not isinstance(boundary, dict):
            return [f"project\t{p.get('id')}\t{p.get('name') or ''}", "boundary\toff"]
        return [
            f"project\t{p.get('id')}\t{p.get('name') or ''}",
            f"content_out\t{boundary.get('content_out')}",
            f"content_in\t{boundary.get('content_in')}",
            f"preset\t{boundary.get('preset')}",
            f"raised_by_minimum\t{_yes_no(boundary.get('raised_by_minimum'))}",
            f"can_change\t{_yes_no(boundary.get('can_change'))}",
            f"summary\t{boundary.get('summary') or ''}",
        ]

    emit(project, json_out=json_out, render=render, lines=lines)


@boundary_app.command("show")
def show_boundary(
    project: str = typer.Argument(..., help="Project id, slug or name."),
    json_out: bool = _json_opt,
) -> None:
    """A project's boundary: the values in force, its own, and what it means for sessions."""
    with scope_session.person_credential(unjoined=True):
        project_id = _project_id(project)
        with _person_sdk() as sdk:
            found = sdk.request("GET", _project_path(project_id))
    _show_boundary(found, json_out=json_out)


@boundary_app.command("set")
def set_boundary(
    project: str = typer.Argument(..., help="Project id, slug or name."),
    preset: Preset | None = typer.Option(
        None,
        "--preset",
        help="internal (no boundary), siloed (a partner project: touching it locks a "
        "session's writes to it, and writes bringing in other content wait for its owners) "
        "or leadership (writes bringing in other content wait for its owners).",
    ),
    out: ContentOut | None = typer.Option(
        None,
        "--out",
        help="Content out: locked (once a session touches it, the session writes only to it) "
        "or allowed.",
    ),
    in_: ContentIn | None = typer.Option(
        None,
        "--in",
        help="Content in, for a write that brings in another project's content: allowed, "
        "owner_approves (held for its owners) or blocked (refused).",
    ),
    yes: bool = _yes_opt,
    json_out: bool = _json_opt,
) -> None:
    """Set a project's boundary, as a preset or as its content out/in values.

    A value not given keeps its own. Needs an owner or admin of the project, or an
    organization admin, acting as a person. Where project boundaries are not turned
    on, exits 4; a caller who may not change it, 3. A boundary below the
    organization's minimum is refused with the minimum. Loosening asks first; --yes
    skips the question.
    """
    scope_session.refuse_inside_conversation(
        "beakr projects boundary set", "Change a project's boundary"
    )
    given = _boundary_given(preset, out, in_, what="the boundary")
    if given is None:
        _usage("Give --preset, or --out and/or --in.")
    assert given is not None
    with scope_session.person_credential(unjoined=True):
        project_id = _project_id(project)
        with _person_sdk() as sdk:
            path = _project_path(project_id)
            current = sdk.request("GET", path)
            boundary = current.get("boundary")
            if not isinstance(boundary, dict):
                raise not_enabled([BOUNDARIES_FEATURE])
            if not boundary.get("can_change"):
                raise _cannot_change(
                    "this project's boundary",
                    "an owner or admin of the project, or an organization admin",
                )
            name = current.get("name") or project_id
            _confirm_loosening(
                [f"{name}'s {change}" for change in _lower(boundary, {**boundary, **given})], yes
            )
            body = {"boundary_preset": preset.value} if preset else {"boundary": given}
            try:
                updated = sdk.request("PATCH", path, json=body)
            except BeakrError as exc:
                raise _explained(exc, [BOUNDARIES_FEATURE]) from exc
    _show_boundary(updated, json_out=json_out)


def _usage(message: str) -> None:
    err_console.print(message, markup=False)
    raise typer.Exit(2)


def register(root: typer.Typer) -> None:
    """Attach ``org isolation``, ``org read-trail``, ``projects boundary`` and the deprecated
    ``projects guard`` (root unused)."""
    org_app.add_typer(isolation_app, name="isolation")
    org_app.add_typer(read_trail_app, name="read-trail")
    projects_app.add_typer(boundary_app, name="boundary")
    projects_app.add_typer(guard_app, name="guard")
