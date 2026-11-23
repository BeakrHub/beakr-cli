"""MCP tools that set a project up from an agent: create it, write its Knowledge profile,
change its card types and the cards it already has, upload the person's files into it, add
the people who work in it, stop queued Knowledge work (``knowledge_queue``,
``cancel_compiles``, ``cancel_sync``; hosted in beakr-engine ``queue_tools.py``), list,
attach, change, preview, sync and remove its connected sources (``project_sources``,
``attach_source``, ``update_source``, ``preview_source_rules``, ``sync_source``,
``remove_source``; hosted in ``source_tools.py``), and draft an interview from its open
review flags (``draft_review_interview``; hosted in ``review_interview_tools.py``).

The hosted MCP server has the same seven tools (beakr-engine
``beakr/api/mcp_remote/project_tools.py``, ``card_type_tools.py`` and
``card_update_tools.py``), and both take each description and tier from the shared agent
tool surface (:mod:`beakr_cli.tool_surface`). ``create_project``, ``set_project_profile``,
``edit_card_type``, ``update_existing_cards`` and ``add_project_member`` answer the same
text on both. The
upload pair differs by design: this server reads the person's own disk (``paths``) and
keeps a long upload running in this process, where the hosted server takes a file's
content in the call (``files``) and reports one stored upload.

Organization invitations are left out on purpose: an agent that could invite any
address into the organization would hand a prompt injection a way out with the
organization's data. A project invitation reaches only the organization's own
auto-join domains, and that one is here.

Uploads run in this process, not in the tool call: Codex ends a call after 60 seconds by
default and a cancelled call would stop the upload with it. ``upload_files`` waits up to
:data:`UPLOAD_WAIT_SECONDS`, reporting MCP progress, then answers with what is done so
far; ``upload_status`` reports the rest.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from beakr_sdk.errors import BeakrError, BeakrUploadInterrupted
from beakr_sdk.resources._upload import (
    _ensure_folder,
    iter_folder_files,
    upload_flow,
    upload_relative_path,
)

from beakr_cli import (
    cancels,
    card_settings,
    card_types,
    card_updates,
    connected_sources,
    review_interview,
)
from beakr_cli.mcp_errors import ApiCallError, call_api
from beakr_cli.mcp_tools_platform import _is_project_id, _project_id, _readable_projects
from beakr_cli.upload_path_policy import (
    MAX_UPLOAD_BYTES,
    named_path_refusal,
    outside_link,
    refused_path,
)
from beakr_cli.upload_placement import (
    REFUSED_PLACEMENT,
    placed,
    placement_fields,
)

if TYPE_CHECKING:
    from beakr_cli.tool_surface import TieredMCPServer

#: Seconds ``upload_files`` waits for its upload before answering with it still running.
UPLOAD_WAIT_SECONDS = 40.0
#: Files one ``upload_files`` call takes; a larger tree is `beakr upload` in a terminal.
MAX_UPLOAD_FILES = 500

#: What to do about a file whose place the server refused (several files of its name
#: in the folder, one the person cannot change): the person decides, in a terminal.
_REFUSED_FIX = (
    "Nothing was created for it. Ask the person which file it should update; in a "
    "terminal, `beakr upload PATH --version-of FILE_ID` adds it to that file and "
    "`beakr upload PATH --on-existing new` keeps it as a separate file."
)

#: Refusals that stop an upload: every later file would meet the same one.
_STOPPING_CODES = frozenset({"insufficient_scope", "not_found"})

ProjectRole = Literal["owner", "admin", "member", "viewer"]


def _error(code: str, message: str, fix: str | None = None, **extra: Any) -> dict[str, Any]:
    return {"status": "error", "error_code": code, "message": message, "fix": fix, **extra}


# --- create_project -------------------------------------------------------------------


async def create_project(
    name: str, description: str = "", card_types: dict[str, Any] | None = None
) -> str | dict[str, Any]:
    """Create a project (described in the shared agent tool surface)."""
    name = (name or "").strip()
    if not name:
        return _error("invalid_arguments", "A project needs a name.", "Pass name.")
    taken = [
        p
        for p in await _readable_projects()
        if str(p.get("name") or "").casefold() == name.casefold()
    ]
    if taken:
        # An agent that retries a create would otherwise make a second project.
        return _error(
            "project_exists",
            f"A project named {name!r} already exists (id: {taken[0].get('id')}).",
            "Use that project (pass its id), or choose another name.",
        )
    chosen = await _card_types_argument(card_types)
    if chosen.get("status") == "error":
        return chosen
    project = await call_api(
        lambda sdk: sdk.projects.create(
            name,
            description=(description or "").strip() or None,
            card_types=chosen or None,
        )
    )
    return created_text(project or {}, name, chosen)


async def _card_types_argument(value: Any) -> dict[str, Any]:
    """``card_types`` as the create route takes it; empty when nothing was chosen.

    A refusal comes back as the error dict itself.
    """
    if not isinstance(value, dict):
        return {}
    card_types: dict[str, Any] = {}
    inherit = str(value.get("inherit") or "").strip()
    if inherit:
        if inherit not in card_settings.INHERIT_VALUES:
            return _error("invalid_arguments", *card_settings.CREATE_REFUSALS["bad_inherit"])
        card_types["inherit"] = inherit
    if isinstance(value.get("agent_may_change"), bool):
        card_types["agent_may_change"] = value["agent_may_change"]
    source = str(value.get("copy_types_from") or "").strip()
    if source:
        card_types["copy_from_project_id"] = await _project_id(source)
    if inherit == "own" and not source:
        # The route would refuse it too; said here in the tool's own argument names.
        return _error("invalid_arguments", *card_settings.CREATE_REFUSALS["own_without_types"])
    return card_types


def created_text(
    project: dict[str, Any], name: str, card_types: dict[str, Any] | None = None
) -> str:
    """What ``create_project`` answers; the hosted server answers the same text."""
    lines = [f"Created project {project.get('name') or name!r} (id: {project.get('id')})."]
    lines += card_settings.created_card_type_lines(card_types)
    if project.get("requires_knowledge_profile"):
        lines.append(
            "The organization requires a Knowledge profile on every new project. Agree the "
            "profile with the person now, entity types first, and save it with "
            "set_project_profile; then upload_files."
        )
    else:
        lines.append(
            "Next, agree its Knowledge profile with the person, entity types first, and save "
            "it with set_project_profile, so Knowledge knows what to build pages for; then "
            "upload_files."
        )
    return "\n".join(lines)


# --- set_project_profile ----------------------------------------------------------------


def _no_profile_yet(exc: ApiCallError) -> bool:
    return exc.response.status_code == 404 and exc.fields.get("error_code") in {
        "knowledge_profile_not_found",
        "not_found",
        None,
    }


async def set_project_profile(
    project: str,
    entity_types: list[str] | None = None,
    goals: list[str] | None = None,
    focus_areas: list[str] | None = None,
    description: str | None = None,
    industry: str | None = None,
    extraction_guidance: str | None = None,
    core_people: list[dict[str, str]] | None = None,
    core_organizations: list[dict[str, str]] | None = None,
    core_topics: list[dict[str, str]] | None = None,
) -> str | dict[str, Any]:
    """Write a project's Knowledge profile (described in the shared agent tool surface)."""
    project_id = await _project_id(project)
    fields = {
        key: value
        for key, value in {
            "entity_types": entity_types,
            "goals": goals,
            "focus_areas": focus_areas,
            "description": description,
            "industry": industry,
            "extraction_guidance": extraction_guidance,
            "core_people": core_people,
            "core_organizations": core_organizations,
            "core_topics": core_topics,
        }.items()
        if value is not None
    }
    if not fields:
        return _error(
            "invalid_arguments",
            "Nothing to write: pass at least one profile field.",
            "Pass entity_types first, then goals, focus_areas and the rest.",
        )
    try:
        await call_api(lambda sdk: sdk.projects.knowledge_profile(project_id))
        exists = True
    except ApiCallError as exc:
        if not _no_profile_yet(exc):
            raise
        exists = False
    if exists:
        saved = await call_api(
            lambda sdk: sdk.projects.update_knowledge_profile(project_id, **fields)
        )
    else:
        if not [t for t in entity_types or [] if str(t).strip()]:
            return _error(
                "entity_types_required",
                "This project has no Knowledge profile yet, and a first profile starts with "
                "its entity types.",
                "Agree with the person what kinds of things Knowledge should keep a page for "
                "(for example 'compound', 'lot', 'deviation'), and pass them as entity_types.",
            )
        saved = await call_api(lambda sdk: sdk.projects.set_knowledge_profile(project_id, **fields))
    details = await call_api(lambda sdk: sdk.projects.get(project_id))
    return profile_saved_text(_dict(saved), _dict(details), created=not exists)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def profile_saved_text(profile: dict[str, Any], project: dict[str, Any], *, created: bool) -> str:
    """What ``set_project_profile`` answers; the hosted server answers the same text."""
    name = project.get("name") or project.get("id")
    lines = [f"{'Saved' if created else 'Updated'} the Knowledge profile of {name}."]
    entity_types = profile.get("entity_types") or []
    lines.append(f"Entity types: {', '.join(map(str, entity_types)) or 'none'}")
    for key, label in (("goals", "Goals"), ("focus_areas", "Focus areas")):
        values = profile.get(key) or []
        if values:
            lines.append(f"{label}: {'; '.join(map(str, values))}")
    for key, label in (
        ("core_people", "People"),
        ("core_organizations", "Organizations"),
        ("core_topics", "Topics"),
    ):
        values = profile.get(key) or []
        if values:
            names = (str(v.get("name") or v) if isinstance(v, dict) else str(v) for v in values)
            lines.append(f"{label}: {', '.join(names)}")
    if project.get("requires_knowledge_profile"):
        lines.append(
            "The organization still counts this profile as unfinished: add a description, an "
            "industry, goals or focus areas with set_project_profile."
        )
    else:
        lines.append("Files can be uploaded now (upload_files).")
    return "\n".join(lines)


# --- edit_card_type ---------------------------------------------------------------------


async def edit_card_type(
    type: str,
    project: str = "",
    org_wide: bool = False,
    label: str | None = None,
    description: str | None = None,
    when_to_use: str | None = None,
    when_not_to_use: str | None = None,
    aliases: list[str] | None = None,
    fields: list[dict[str, Any] | str] | None = None,
    grain: Literal["card", "row"] | None = None,
    status: Literal["active", "deprecated"] | None = None,
    reset: bool = False,
) -> str | dict[str, Any]:
    """Change one card type (described in the shared agent tool surface).

    The call is the signed-in person's own ``PATCH`` or ``DELETE
    /v1/knowledge/wiki/page-types/{key}``: that route decides who may change a
    type. The key is checked here because it becomes a path segment, and the
    SDK percent-encodes it.
    """
    key = str(type or "").strip()
    if not key:
        return _error("invalid_arguments", *card_types.NO_TYPE)
    if not card_types.valid_type_key(key):
        return _error("invalid_arguments", *card_types.BAD_TYPE_KEY)
    # Not `scope`: on every tool here that word is another name for the project
    # (mcp_call_shaping.PROJECT_NAMES), so an agent sends its project under it.
    scope = "org" if org_wide is True else "project"
    project_id: str | None = None
    if scope == "project":
        if not str(project or "").strip():
            return _error("invalid_arguments", *card_types.NO_PROJECT)
        project_id = await _project_id(project)
    elif project:
        return _error("invalid_arguments", *card_types.ORG_WITH_PROJECT)
    if reset:
        await call_api(
            lambda sdk: sdk.knowledge.remove_card_type_copy(key, scope=scope, project_id=project_id)
        )
        return card_types.card_type_reset_text(key, scope)
    changes = card_types.changes(
        {
            "label": label,
            "description": description,
            "when_to_use": when_to_use,
            "when_not_to_use": when_not_to_use,
            "aliases": aliases,
            "fields": fields,
            "grain": grain,
            "status": status,
        }
    )
    if not changes:
        return _error("invalid_arguments", *card_types.NOTHING_TO_CHANGE)
    saved = await call_api(
        lambda sdk: sdk.knowledge.update_card_type(
            key, scope=scope, project_id=project_id, **changes
        )
    )
    return card_types.card_type_saved_text(_dict(_dict(saved).get("page_type")), scope)


# --- card_type_settings -----------------------------------------------------------------


async def card_type_settings(
    project: str,
    inherit: str | None = None,
    agent_may_change: Any = None,
    keep_types_in_use: Any = None,
    apply: Any = None,
) -> str | dict[str, Any]:
    """Change a project's card type settings (described in the shared agent tool surface).

    The call is the signed-in person's own ``PUT
    /v1/knowledge/wiki/page-types/settings``: that route decides who may.
    Without ``apply=true`` it sends a dry run and nothing is written.
    """
    changes: dict[str, Any] = {}
    if inherit is not None and str(inherit).strip():
        inherit = str(inherit).strip()
        if inherit not in card_settings.INHERIT_VALUES:
            return _error("invalid_arguments", *card_settings.REFUSALS["bad_inherit"])
        changes["inherit"] = inherit
    if agent_may_change is not None:
        if not isinstance(agent_may_change, bool):
            return _error("invalid_arguments", *card_settings.REFUSALS["bad_agent_may_change"])
        changes["agent_may_change"] = agent_may_change
    if not changes:
        return _error("invalid_arguments", *card_settings.REFUSALS["no_change"])
    if not str(project or "").strip():
        return _error("invalid_arguments", *card_types.NO_PROJECT)
    project_id = await _project_id(project)
    answer = await call_api(
        lambda sdk: sdk.knowledge.set_card_type_settings(
            project_id,
            keep_types_in_use=keep_types_in_use is True,
            # Only apply=true writes: anything else, a missing value included, previews.
            dry_run=apply is not True,
            **changes,
        )
    )
    return card_settings.settings_text(_dict(answer))


# --- update_existing_cards --------------------------------------------------------------


async def update_existing_cards(
    type: str,
    project: str,
    change: str,
    renames: list[dict[str, Any]] | dict[str, str] | None = None,
    to_type: str | None = None,
    apply: Any = None,
) -> str | dict[str, Any]:
    """Change the cards a project already has (described in the shared agent tool surface).

    The call is the signed-in person's own ``POST
    /v1/knowledge/wiki/page-types/{key}/rename-fields`` or ``.../retype-cards``:
    those routes decide who may. Without ``apply=true`` it sends a dry run and
    nothing is written.
    """
    key = str(type or "").strip()
    if not key:
        return _error("invalid_arguments", *card_updates.REFUSALS["no_type"])
    if not card_types.valid_type_key(key):
        return _error("invalid_arguments", *card_updates.REFUSALS["bad_type"])
    change = str(change or "").strip()
    if change not in card_updates.CHANGES:
        return _error("invalid_arguments", *card_updates.REFUSALS["no_change"])
    pairs: list[dict[str, str]] = []
    target = ""
    if change == "rename_fields":
        pairs = card_updates.renames(renames)
        if not pairs:
            return _error("invalid_arguments", *card_updates.REFUSALS["no_renames"])
    else:
        target = str(to_type or "").strip()
        if not target:
            return _error("invalid_arguments", *card_updates.REFUSALS["no_to_type"])
        if not card_types.valid_type_key(target):
            return _error("invalid_arguments", *card_updates.REFUSALS["bad_to_type"])
    if not str(project or "").strip():
        return _error("invalid_arguments", *card_types.NO_PROJECT)
    project_id = await _project_id(project)
    # Only apply=true writes: anything else, a missing value included, previews.
    dry_run = apply is not True

    async def send(continue_event_id: str | None = None) -> dict[str, Any]:
        if change == "rename_fields":
            answer = await call_api(
                lambda sdk: sdk.knowledge.rename_card_type_fields(
                    key, project_id, pairs, dry_run=dry_run, continue_event_id=continue_event_id
                )
            )
        else:
            answer = await call_api(
                lambda sdk: sdk.knowledge.retype_cards(
                    key, project_id, target, dry_run=dry_run, continue_event_id=continue_event_id
                )
            )
        return _dict(answer)

    result = await send()
    if not dry_run:
        # One call writes a bounded number of cards; keep going as one change.
        result = await card_updates.until_done_async(
            result, send, written=card_updates.WRITTEN[change]
        )
    if change == "rename_fields":
        return card_updates.fields_renamed_text(result)
    return card_updates.cards_retyped_text(result)


# --- upload_files -----------------------------------------------------------------------


def _refused_path(path: Path) -> str | None:
    """Why an agent may not upload ``path``, or None (:mod:`beakr_cli.upload_path_policy`)."""
    return refused_path(path)


@dataclass
class _Item:
    path: Path
    root: Path | None
    size: int
    #: For a file named by a relative path inside the working folder: the
    #: folders it sits in there (``sub`` for ``sub/valve-note.txt``), recreated
    #: in the project as a folder upload recreates its tree.
    folder: Path | None = None
    #: The path as the call named it, when that is not the absolute path sent.
    given: str | None = None


@dataclass
class _Upload:
    id: str
    project_id: str
    project_name: str
    folder_id: str | None
    ingest: bool
    items: list[_Item]
    total_bytes: int
    sent: list[dict[str, Any]] = field(default_factory=list)
    failed: list[dict[str, Any]] = field(default_factory=list)
    sent_bytes: int = 0
    stopped: dict[str, Any] | None = None
    started: float = field(default_factory=time.monotonic)
    finished: float | None = None
    task: asyncio.Task | None = None
    #: Sent on each completion: the agent wrote these files (``written_by_agent``).
    written_by_agent: bool = False


#: Uploads this server has run, by id, for ``upload_status``.
_UPLOADS: dict[str, _Upload] = {}


def _gather(paths: list[str]) -> tuple[list[_Item], list[dict[str, Any]]]:
    """The files to send, and the paths refused or skipped, with why."""
    items: list[_Item] = []
    skipped: list[dict[str, Any]] = []
    cwd = Path.cwd()
    for given in paths:
        path = Path(str(given)).expanduser()
        if not path.is_absolute():
            path = cwd / path
        # Before the path is looked at: a refused place is not even said to exist.
        refused = _refused_path(path) or named_path_refusal(str(given), path, cwd)
        if refused:
            skipped.append({"path": str(given), "reason": f"not uploaded: {refused}"})
            continue
        if not path.exists():
            skipped.append({"path": str(given), "reason": "no such file or folder"})
            continue
        if path.is_dir():
            for file_path in iter_folder_files(path):
                refused = outside_link(file_path, path) or _refused_path(file_path)
                if refused:
                    skipped.append({"path": str(file_path), "reason": f"not uploaded: {refused}"})
                    continue
                items.append(_Item(file_path, path, file_path.stat().st_size))
        else:
            items.append(_named_file(str(given), path, cwd))
    kept = []
    for item in items:
        if item.size == 0:
            skipped.append({"path": str(item.path), "reason": "empty file"})
        else:
            kept.append(item)
    return kept, skipped


def _named_file(given: str, path: Path, cwd: Path) -> _Item:
    """A file the call named, with the folders a relative path inside ``cwd`` gives it.

    ``sub/valve-note.txt`` landed at the project's root as ``valve-note.txt``
    (sandbox run, 2026-11-14), where a folder upload keeps each file's folders.
    It now lands in ``sub``, as ``sub`` uploaded as a folder would put it.
    """
    item = _Item(path, None, path.stat().st_size)
    if str(path) != given:
        item.given = given
    relative = Path(given)
    if relative.is_absolute() or given.startswith("~"):
        return item
    try:
        inside = path.resolve().relative_to(cwd.resolve())
    except ValueError:
        return item
    if ".." not in relative.parts and inside.parent != Path("."):
        item.folder = inside.parent
    return item


def _relative_dir(item: _Item) -> Path | None:
    """The folder ``item`` lands in under the call's folder, or None for that folder itself."""
    if item.root is not None:
        return Path(item.root.name) / item.path.parent.relative_to(item.root)
    return item.folder


def _file_flow(upload: _Upload, item: _Item, folders: dict[Path, str | None], children: dict):
    """One file's upload, into its folder (recreated from a folder upload's tree)."""
    target = upload.folder_id
    relative_dir = _relative_dir(item)
    if relative_dir is not None:
        target = yield from _ensure_folder(relative_dir, folders, upload.project_id, children)
    recorded = upload_relative_path(item.path, item.root)
    if item.folder is not None:
        recorded = (item.folder / item.path.name).as_posix()
    # A completion refused for where the file would go is aborted, not left open:
    # nothing here completes it again, and an open upload counts against the person's.
    return (
        yield from placed(
            upload_flow(
                item.path,
                project_id=upload.project_id,
                folder_id=target,
                ingest=upload.ingest,
                relative_path=recorded,
            ),
            placement_fields(written_by_agent=upload.written_by_agent),
            abort_refused=True,
        )
    )


async def _run_upload(upload: _Upload) -> None:
    folders: dict[Path, str | None] = {Path("."): upload.folder_id}
    children: dict[str, dict[str, str]] = {}
    try:
        for item in upload.items:
            try:
                result = await call_api(
                    lambda sdk, i=item: sdk.files._drive(_file_flow(upload, i, folders, children))
                )
            except ApiCallError as exc:
                failure = {"path": str(item.path), **exc.fields}
                upload.failed.append(failure)
                if exc.fields.get("error_code") in REFUSED_PLACEMENT:
                    # This file's place only (a 403 file_not_writable included):
                    # the next file goes to another path.
                    continue
                if exc.fields.get("error_code") in _STOPPING_CODES or exc.response.status_code in {
                    401,
                    403,
                }:
                    upload.stopped = exc.fields
                    break
                continue
            except BeakrUploadInterrupted as exc:
                upload.failed.append(
                    {
                        "path": str(item.path),
                        "error_code": "upload_interrupted",
                        "message": str(exc),
                        "upload_id": exc.upload_id,
                    }
                )
                continue
            except (OSError, ValueError, BeakrError) as exc:
                upload.failed.append(
                    {"path": str(item.path), "error_code": "upload_failed", "message": str(exc)}
                )
                continue
            artifact = result.get("artifact") or {}
            upload.sent.append(
                {
                    "path": str(item.path),
                    "file_id": artifact.get("id"),
                    "job_id": result.get("job_id"),
                    # created / versioned / restored / unchanged, and its version.
                    "result": result.get("result"),
                    "version_no": result.get("version_no"),
                    "given": item.given,
                    # Only a named file's: a folder upload's tree is the folder's own.
                    "folder": item.folder,
                }
            )
            upload.sent_bytes += item.size
    except Exception as exc:  # noqa: BLE001 - kept for upload_status, never lost in a task
        upload.stopped = {"error_code": "upload_failed", "message": str(exc), "fix": None}
    finally:
        upload.finished = time.monotonic()


async def _report_progress(upload: _Upload) -> None:
    """MCP progress for the call that started ``upload``, when its client asked for it."""
    if _SERVER is None:
        return
    try:
        context = _SERVER.get_context()
        await context.report_progress(
            upload.sent_bytes,
            upload.total_bytes,
            f"{len(upload.sent)} of {len(upload.items)} files sent",
        )
    except Exception:  # noqa: BLE001 - progress is a courtesy; the upload goes on
        return


async def upload_files(
    project: str,
    paths: list[str],
    folder_id: str = "",
    knowledge: bool = True,
    resume_upload_id: str = "",
    written_by_agent: bool = False,
) -> str | dict[str, Any]:
    """Upload local files or folders into a project (described in the shared surface)."""
    project_id = await _project_id(project)
    named = next(
        (p for p in await _readable_projects() if str(p.get("id")) == project_id),
        {"name": project},
    )
    project_name = str(named.get("name") or project)
    if not paths:
        return _error("invalid_arguments", "No paths given.", "Pass paths: files or folders.")
    if resume_upload_id:
        return await _resume(
            resume_upload_id, paths, project_name, written_by_agent=written_by_agent is True
        )
    items, skipped = _gather(paths)
    if len(items) > MAX_UPLOAD_FILES:
        return _error(
            "too_many_files",
            f"{len(items)} files is more than one call uploads ({MAX_UPLOAD_FILES}).",
            "Upload subfolders one call at a time, or ask the person to run "
            f"`beakr upload PATH --project {project_id}` in a terminal.",
        )
    total_bytes = sum(item.size for item in items)
    if total_bytes > MAX_UPLOAD_BYTES:
        return _error(
            "upload_too_large",
            f"{_size(total_bytes)} in {len(items)} files is more than one call uploads "
            f"({_size(MAX_UPLOAD_BYTES)}).",
            "Upload fewer files or one subfolder a call, or ask the person to run "
            f"`beakr upload PATH --project {project_id}` in a terminal, which has no such limit.",
            skipped=skipped,
        )
    if not items:
        # Each path's own reason: "none can be uploaded" alone left the agent
        # guessing which rule it met (sandbox run, 2026-11-14).
        reasons = "; ".join(f"{row['path']}: {row['reason']}" for row in skipped[:20])
        return _error(
            "nothing_to_upload",
            f"Nothing was uploaded. {reasons}." if reasons else "Nothing was uploaded.",
            "Check the paths; hidden files, key and credential files and empty files are "
            "never uploaded from here.",
            skipped=skipped,
        )
    upload = _Upload(
        id=f"up_{uuid.uuid4().hex[:12]}",
        project_id=project_id,
        project_name=project_name,
        folder_id=(folder_id or "").strip() or None,
        ingest=knowledge,
        items=items,
        total_bytes=total_bytes,
        written_by_agent=written_by_agent is True,
    )
    _UPLOADS[upload.id] = upload
    # Not the call's own task: a client that gives up on the call (Codex after 60
    # seconds) must not cancel the upload.
    upload.task = asyncio.get_running_loop().create_task(_run_upload(upload))
    deadline = time.monotonic() + UPLOAD_WAIT_SECONDS
    while not upload.task.done() and time.monotonic() < deadline:
        await asyncio.wait({upload.task}, timeout=2.0)
        await _report_progress(upload)
    return _upload_text(upload, skipped=skipped)


async def _resume(
    upload_id: str, paths: list[str], project_name: str, *, written_by_agent: bool = False
) -> str | dict[str, Any]:
    if len(paths) != 1:
        return _error(
            "invalid_arguments",
            "resume_upload_id finishes one file: pass exactly the one path that was interrupted.",
        )
    path = Path(paths[0]).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    refused = _refused_path(path)
    if refused or not path.is_file():
        return _error("invalid_arguments", f"Cannot resume {paths[0]}: {refused or 'not a file'}.")
    try:
        result = await call_api(
            lambda sdk: sdk.files.resume_upload(upload_id, path, written_by_agent=written_by_agent)
        )
    except BeakrUploadInterrupted as exc:
        return _error(
            "upload_interrupted",
            str(exc),
            f"Retry with resume_upload_id={exc.upload_id!r} and the same path.",
            upload_id=exc.upload_id,
        )
    artifact = (result or {}).get("artifact") or {}
    return (
        f"Finished uploading {path.name} to {project_name} (file {artifact.get('id')})"
        f"{_placement_words(result or {})}."
    )


def _placement_words(row: dict[str, Any]) -> str:
    """`` (a new version, 2, of the file already there)`` and the like; empty for a new file."""
    result = row.get("result")
    version = row.get("version_no")
    if result == "versioned":
        return (
            f", added as version {version} of the file already there"
            if version
            else (", added as a new version of the file already there")
        )
    if result == "restored":
        return (
            f": the same as its version {version}, which is current again"
            if version
            else (": the same as an earlier version, which is current again")
        )
    if result == "unchanged":
        return ": unchanged, the same as the file's current version"
    return ""


def _sent_line(row: dict[str, Any]) -> str:
    """One sent file: its name, where it landed, its id, and the file it was.

    A path the call named some other way (``../notes/a.txt``, ``sub/b.txt``) is
    followed by the absolute path that was read, so a file is never reported
    under a name that does not say what it is.
    """
    name = Path(row["path"]).name
    folder = row.get("folder")
    where = f" (in folder {Path(folder).as_posix()}/)" if folder else ""
    line = f"- {name}{where}: file {row['file_id']}{_placement_words(row)}"
    if row.get("given"):
        line += f" (read from {Path(row['path']).resolve()})"
    return line


def _size(count: float) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if count < 1024 or unit == "GB":
            return f"{count} {unit}" if unit == "bytes" else f"{count:.1f} {unit}"
        count /= 1024
    return str(count)


def _upload_text(upload: _Upload, *, skipped: list[dict[str, Any]] | None = None) -> str:
    total = len(upload.items)
    lines = []
    if upload.finished is None:
        lines.append(
            f"Uploading to {upload.project_name}: {len(upload.sent)} of {total} files sent "
            f"({_size(upload.sent_bytes)} of {_size(upload.total_bytes)}). It carries on in the "
            f"background: call upload_status with upload_id {upload.id!r} to see when it "
            "finishes. Do not upload these files again."
        )
    else:
        lines.append(
            f"Uploaded {len(upload.sent)} of {total} files ({_size(upload.sent_bytes)}) to "
            f"{upload.project_name} (upload {upload.id})."
        )
    for row in upload.sent[:50]:
        lines.append(_sent_line(row))
    if len(upload.sent) > 50:
        lines.append(f"- ... and {len(upload.sent) - 50} more")
    for row in upload.failed[:20]:
        text = f"- Failed: {row['path']}: {row.get('message')}"
        if row.get("error_code") in REFUSED_PLACEMENT:
            # The API's fix names completion options this tool does not take.
            text = f"- Not uploaded: {row['path']}: {row.get('message')} {_REFUSED_FIX}"
        elif row.get("upload_id"):
            text += f" (resume it: upload_files with resume_upload_id={row['upload_id']!r})"
        lines.append(text)
    for row in (skipped or [])[:20]:
        lines.append(f"- Skipped: {row['path']}: {row['reason']}")
    if upload.stopped is not None:
        stopped = upload.stopped
        lines.append(f"Stopped: {stopped.get('message')} {stopped.get('fix') or ''}".rstrip())
    elif upload.finished is not None and upload.sent and upload.ingest:
        lines.append(
            "Knowledge compiles from them in the background, usually within a few minutes; "
            "list_projects says while it is still building."
        )
    return "\n".join(lines)


async def upload_status(upload_id: str) -> str | dict[str, Any]:
    """Report an upload started by ``upload_files`` (described in the shared surface)."""
    upload = _UPLOADS.get((upload_id or "").strip())
    if upload is None:
        return _error(
            "upload_not_found",
            f"No upload {upload_id!r} in this server.",
            "Pass the upload_id upload_files gave. Uploads are known only to the server that "
            "ran them; after a restart, list the project's files with documents.",
        )
    return _upload_text(upload)


# --- add_project_member -----------------------------------------------------------------


async def _org_members() -> list[dict[str, Any]]:
    """The organization's members; none when this credential cannot list them."""
    try:
        members = await call_api(lambda sdk: sdk.members.list())
    except Exception:  # noqa: BLE001 - no lookup: an email is then invited, an id sent as is
        return []
    return [m for m in members if isinstance(m, dict)] if isinstance(members, list) else []


def find_member(
    members: list[dict[str, Any]], person: str
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """The member ``person`` names (email, user id or exact name), and any name clashes."""
    wanted = person.strip().casefold()
    by_email = [m for m in members if str(m.get("email") or "").casefold() == wanted]
    if by_email:
        return by_email[0], []
    by_id = [m for m in members if str(m.get("user_id") or "").casefold() == wanted]
    if by_id:
        return by_id[0], []
    by_name = [m for m in members if str(m.get("display_name") or "").casefold() == wanted]
    if len(by_name) == 1:
        return by_name[0], []
    return None, by_name


async def add_project_member(
    project: str, person: str, role: ProjectRole | None = None
) -> str | dict[str, Any]:
    """Add a person to a project, or invite them (described in the shared surface)."""
    person = (person or "").strip()
    if not person:
        return _error("invalid_arguments", "Name the person: an email address.", "Pass person.")
    project_id = await _project_id(project)
    found, clashes = find_member(await _org_members(), person)
    if clashes:
        listed = "; ".join(f"{m.get('display_name')} <{m.get('email')}>" for m in clashes[:5])
        return _error(
            "person_ambiguous",
            f"More than one member is named {person!r}: {listed}.",
            "Pass their email address.",
        )
    if found is None and _is_project_id(person):
        # A user id this credential could not look up: the API checks it.
        found = {"user_id": person}
    if found is not None:
        added = await call_api(
            lambda sdk: sdk.projects.add_member(project_id, str(found["user_id"]), role=role)
        )
        who = found.get("display_name") or found.get("email") or found["user_id"]
        if found.get("email") and found.get("display_name"):
            who = f"{found['display_name']} <{found['email']}>"
        given = _dict(added).get("role") or role or "member"
        return f"Added {who} to the project as {given}."
    if "@" not in person:
        return _error(
            "person_not_found",
            f"No member of this organization matches {person!r}.",
            "Pass their email address: someone not in the organization yet is invited by email.",
        )
    try:
        await call_api(lambda sdk: sdk.projects.invite(project_id, person, role=role or "member"))
    except ApiCallError as exc:
        refusal = invitation_refusal(person, str(exc.fields.get("message") or ""))
        if refusal is not None:
            return refusal
        raise
    return (
        f"Invited {person} to the project as {role or 'member'}: they are added when they "
        "first sign in to Beakr."
    )


def invitation_refusal(person: str, message: str) -> dict[str, Any] | None:
    """A refused project invitation in the tool's own words, or None for any other refusal."""
    if "already a member of the organization" in message:
        return _error(
            "person_lookup_unavailable",
            f"{person} is in the organization, but this credential cannot look up its "
            "members to add them.",
            "Pass their user id instead, or ask the person to add them in the web app.",
        )
    if "auto-join" in message:
        return _error(
            "invitation_domain_refused",
            message.split(" Use Share")[0],
            "Only an organization admin can bring in someone from another domain: in the "
            "Beakr web app, Settings, Members. This tool does not send organization "
            "invitations.",
        )
    return None


#: The server ``upload_files`` reports progress through; set by :func:`register`.
_SERVER: TieredMCPServer | None = None

# --- knowledge_queue, cancel_compiles, cancel_sync -------------------------------------
#
# Stopping queued Knowledge work, to stop paying for it. The hosted halves are
# beakr-engine ``beakr/api/mcp_remote/queue_tools.py``; the text is
# :mod:`beakr_cli.cancels`, the same for the same REST answer. Each call is the
# signed-in person's own request to a public route, which decides who may.


def _cancel_source(
    attachment_id: str | None, artifact_id: str | None
) -> dict[str, str] | dict[str, Any]:
    """``{attachment_id}`` or ``{artifact_id}`` or ``{}``; an error dict for both."""
    attachment = str(attachment_id or "").strip()
    artifact = str(artifact_id or "").strip()
    if attachment and artifact:
        return _error(
            "invalid_arguments",
            "Name one source: attachment_id or artifact_id, not both.",
            "Pass one of them, or neither for the whole project.",
        )
    if attachment:
        return {"attachment_id": attachment}
    if artifact:
        return {"artifact_id": artifact}
    return {}


async def knowledge_queue(
    project: str, attachment_id: str | None = None, artifact_id: str | None = None
) -> str | dict[str, Any]:
    """Count a project's queued and running compiles (described in the shared surface)."""
    source = _cancel_source(attachment_id, artifact_id)
    if source.get("status") == "error":
        return source
    project_id = await _project_id(project)
    queue = await call_api(lambda sdk: cancels.compile_queue(sdk, project_id, **source))
    return cancels.queue_text(_dict(queue), source=bool(source))


async def cancel_compiles(
    project: str, attachment_id: str | None = None, artifact_id: str | None = None
) -> str | dict[str, Any]:
    """Cancel a project's or one source's queued compiles (described in the shared surface)."""
    source = _cancel_source(attachment_id, artifact_id)
    if source.get("status") == "error":
        return source
    project_id = await _project_id(project)
    answer = await call_api(lambda sdk: cancels.cancel_queued_compiles(sdk, project_id, **source))
    return cancels.cancel_compiles_text(_dict(answer))


async def cancel_sync(project: str, attachment_id: str) -> str | dict[str, Any]:
    """Cancel a source's running sync (described in the shared surface)."""
    attachment = str(attachment_id or "").strip()
    if not attachment:
        return _error(
            "invalid_arguments",
            "Name the source: its attachment_id.",
            "Pass attachment_id, the connected source's attachment id in the project.",
        )
    project_id = await _project_id(project)
    answer = await call_api(lambda sdk: cancels.cancel_sync(sdk, project_id, attachment))
    return cancels.cancel_sync_text(_dict(answer))


# --- project_sources, attach_source, update_source, preview_source_rules, sync_source,
# --- remove_source ----------------------------------------------------------------------
#
# A project's connected sources, so setting a project up can be scripted. The hosted
# halves are beakr-engine ``beakr/api/mcp_remote/source_tools.py``; the text is
# :mod:`beakr_cli.connected_sources`, the same for the same REST answer. Each call is
# the signed-in person's own request to the routes ``beakr sources`` uses, which decide
# who may.
#
# Attaching leaves Knowledge OFF unless the caller turns it on: turning it on starts a
# sync, which reads the source and spends the organization's ingest budget, so it is a
# step an agent takes on purpose and not a side effect of attaching.


def _given_filter(value: Any) -> dict[str, Any] | None:
    """A filter that says something; ``{}`` is a model filling an optional property."""
    return value if isinstance(value, dict) and value else None


async def _connected_sources(project_id: str) -> tuple[list[dict[str, Any]], bool]:
    """The project's sources, and whether more exist than the pages read."""
    sources: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(connected_sources.PAGES):
        page = _dict(
            await call_api(lambda sdk, c=cursor: connected_sources.sources_page(sdk, project_id, c))
        )
        sources.extend(_dict(item) for item in page.get("data") or [])
        cursor = page.get("next_cursor")
        if not cursor:
            break
    return sources, bool(cursor)


async def project_sources(project: str, attachment_id: str | None = None) -> str | dict[str, Any]:
    """List a project's connected sources, or one and its sync (described in the surface)."""
    project_id = await _project_id(project)
    attachment = connected_sources.attachment_id(attachment_id)
    if str(attachment_id or "").strip() and not attachment:
        return _error(*connected_sources.NAME_SOURCE)
    sources, more = await _connected_sources(project_id)
    if not attachment:
        return connected_sources.sources_text(sources, more=more)
    source = next((item for item in sources if str(item.get("id")) == attachment), None)
    if source is None:
        return _error(*connected_sources.NOT_ATTACHED)
    status = await call_api(lambda sdk: sdk.project_sources.status(project_id, attachment))
    return "\n".join(
        [
            connected_sources.source_line(source),
            connected_sources.sync_status_text(_dict(status)),
        ]
    )


async def attach_source(
    project: str,
    provider: str,
    item_id: str,
    item_type: str,
    name: str,
    url: str | None = None,
    connector_id: str | None = None,
    knowledge: bool | None = None,
    nightly_sync: bool | None = None,
    filter: dict[str, Any] | None = None,
    guidance: str | None = None,
) -> str | dict[str, Any]:
    """Attach a source to a project, Knowledge off unless asked (described in the surface)."""
    provider = str(provider or "").strip()
    item_id = str(item_id or "").strip()
    item_type = str(item_type or "").strip()
    name = str(name or "").strip()
    if not (provider and item_id and item_type and name):
        return _error(*connected_sources.ATTACH_NEEDS)
    project_id = await _project_id(project)
    # Off unless asked: turning Knowledge on is what starts reading and spending.
    reads = knowledge is True
    source = await call_api(
        lambda sdk: sdk.project_sources.attach(
            project_id,
            provider=provider,
            external_item_id=item_id,
            external_item_type=item_type,
            external_item_name=name,
            external_item_url=str(url or "").strip() or None,
            connector_id=str(connector_id or "").strip() or None,
            ingest_wiki=reads,
            auto_sync_wiki=reads and nightly_sync is True,
            source_query=_given_filter(filter),
            guidance=str(guidance or "").strip() or None,
        )
    )
    return connected_sources.attached_text(_dict(source))


async def update_source(
    project: str,
    attachment_id: str,
    knowledge: bool | None = None,
    nightly_sync: bool | None = None,
    filter: dict[str, Any] | None = None,
    guidance: str | None = None,
    clear_filter: bool | None = None,
    clear_guidance: bool | None = None,
) -> str | dict[str, Any]:
    """Change a connected source's settings, filter or guidance (described in the surface)."""
    attachment = connected_sources.attachment_id(attachment_id)
    if not attachment:
        return _error(*connected_sources.NAME_SOURCE)
    changes: dict[str, Any] = {}
    clear: list[str] = []
    if isinstance(knowledge, bool):
        changes["ingest_wiki"] = knowledge
    if isinstance(nightly_sync, bool):
        changes["auto_sync_wiki"] = nightly_sync
    # An empty value never clears: a model that fills every optional property
    # sends {} and "" for what it is not changing. Clearing is asked for by name.
    if clear_filter is True:
        clear.append("source_query")
    elif _given_filter(filter):
        changes["source_query"] = filter
    if clear_guidance is True:
        clear.append("guidance")
    elif str(guidance or "").strip():
        changes["guidance"] = str(guidance).strip()
    if not changes and not clear:
        return _error(*connected_sources.NOTHING_TO_CHANGE)
    project_id = await _project_id(project)
    source = await call_api(
        lambda sdk: sdk.project_sources.update(project_id, attachment, **changes, clear=clear)
    )
    return connected_sources.updated_text(
        _dict(source), turned_on=changes.get("ingest_wiki") is True
    )


async def preview_source_rules(
    project: str, attachment_id: str, filter: dict[str, Any] | None = None
) -> str | dict[str, Any]:
    """Count what a source's filter keeps; nothing changes (described in the surface)."""
    attachment = connected_sources.attachment_id(attachment_id)
    if not attachment:
        return _error(*connected_sources.NAME_SOURCE)
    project_id = await _project_id(project)
    # Left out, the saved filter is previewed.
    candidate = {"source_query": filter} if _given_filter(filter) else {}
    preview = await call_api(
        lambda sdk: sdk.project_sources.preview(project_id, attachment, **candidate)
    )
    return connected_sources.preview_text(_dict(preview))


async def sync_source(project: str, attachment_id: str) -> str | dict[str, Any]:
    """Start a sync of a connected source now (described in the shared surface)."""
    attachment = connected_sources.attachment_id(attachment_id)
    if not attachment:
        return _error(*connected_sources.NAME_SOURCE)
    project_id = await _project_id(project)
    answer = await call_api(lambda sdk: sdk.project_sources.sync(project_id, attachment))
    return connected_sources.sync_started_text(_dict(answer))


async def remove_source(project: str, attachment_id: str) -> str | dict[str, Any]:
    """Remove a connected source from a project (described in the shared surface)."""
    attachment = connected_sources.attachment_id(attachment_id)
    if not attachment:
        return _error(*connected_sources.NAME_SOURCE)
    project_id = await _project_id(project)
    await call_api(lambda sdk: sdk.project_sources.remove(project_id, attachment))
    return connected_sources.removed_text(attachment)


# --- draft_review_interview -------------------------------------------------------------
#
# One question per open review flag, saved as a draft interview the person edits and
# sends in Beakr. The hosted half is beakr-engine
# ``beakr/api/mcp_remote/review_interview_tools.py``; the text is
# :func:`beakr_cli.review_interview.review_interview_text`, the same for the same REST
# answer. Drafting only: nothing here can send an interview.

ReviewKind = Literal["contradiction", "ambiguity", "unverifiable", "likely_error", "other"]


async def draft_review_interview(
    project: str,
    flags: list[str] | None = None,
    kinds: list[ReviewKind] | None = None,
    title: str | None = None,
) -> str | dict[str, Any]:
    """Draft an interview from a project's open review flags (described in the shared surface).

    Drafting only, as on the hosted server: no participant, so no link is made;
    the person sends it from Beakr.
    """
    project_id = await _project_id(project)
    body = review_interview.request_body(proposal_ids=flags, kinds=kinds, title=title)
    answer = await call_api(lambda sdk: review_interview.draft_from_reviews(sdk, project_id, body))
    return review_interview.review_interview_text(_dict(answer))


PROJECT_TOOLS = (
    create_project,
    set_project_profile,
    edit_card_type,
    update_existing_cards,
    card_type_settings,
    upload_files,
    upload_status,
    add_project_member,
    knowledge_queue,
    cancel_compiles,
    cancel_sync,
    project_sources,
    attach_source,
    update_source,
    preview_source_rules,
    sync_source,
    remove_source,
    draft_review_interview,
)


def register(server: TieredMCPServer) -> None:
    """Register this module's tools on ``server``.

    Description, tier, title and MCP annotations all come from the shared tool
    surface (``tool_surface.tool_kwargs``), the hints the hosted server lists.
    """
    from beakr_cli import tool_surface

    global _SERVER
    _SERVER = server
    for tool in PROJECT_TOOLS:
        server.tool(**tool_surface.tool_kwargs(tool.__name__))(tool)
