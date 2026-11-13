"""MCP tools that set a project up from an agent: create it, write its Knowledge profile,
upload the person's files into it, and add the people who work in it.

The hosted MCP server has the same five tools (beakr-engine
``beakr/api/mcp_remote/project_tools.py``), and both take each description and tier
from the shared agent tool surface (:mod:`beakr_cli.tool_surface`). ``create_project``,
``set_project_profile`` and ``add_project_member`` answer the same text on both. The
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
import fnmatch
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

from beakr_cli.mcp_errors import ApiCallError, call_api
from beakr_cli.mcp_tools_platform import _is_project_id, _project_id, _readable_projects

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

#: Seconds ``upload_files`` waits for its upload before answering with it still running.
UPLOAD_WAIT_SECONDS = 40.0
#: Files one ``upload_files`` call takes; a larger tree is `beakr upload` in a terminal.
MAX_UPLOAD_FILES = 500

#: Folders never uploaded from, wherever they sit: credential stores, settings, git's own.
_SECRET_DIRECTORIES = frozenset(
    {
        ".ssh",
        ".aws",
        ".azure",
        ".gnupg",
        ".kube",
        ".docker",
        ".beakr",
        ".config",
        ".git",
        ".password-store",
        ".terraform",
    }
)
#: File names never uploaded: keys, keystores and credential files.
_SECRET_NAMES = (
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.jks",
    "*.keystore",
    "*.kdbx",
    "*.ovpn",
    "id_rsa*",
    "id_dsa*",
    "id_ecdsa*",
    "id_ed25519*",
    "credentials",
    "credentials.json",
)
#: Refusals that stop an upload: every later file would meet the same one.
_STOPPING_CODES = frozenset({"insufficient_scope", "not_found"})

ProjectRole = Literal["owner", "admin", "member", "viewer"]

#: Each tool's MCP annotations: all of them change something but ``upload_status``; none
#: deletes.
ANNOTATIONS: dict[str, dict[str, bool]] = {
    "create_project": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
    "set_project_profile": {
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
    },
    "upload_files": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False},
    "upload_status": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
    "add_project_member": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
}


def _error(code: str, message: str, fix: str | None = None, **extra: Any) -> dict[str, Any]:
    return {"status": "error", "error_code": code, "message": message, "fix": fix, **extra}


# --- create_project -------------------------------------------------------------------


async def create_project(name: str, description: str = "") -> str | dict[str, Any]:
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
    project = await call_api(
        lambda sdk: sdk.projects.create(name, description=(description or "").strip() or None)
    )
    return created_text(project or {}, name)


def created_text(project: dict[str, Any], name: str) -> str:
    """What ``create_project`` answers; the hosted server answers the same text."""
    lines = [f"Created project {project.get('name') or name!r} (id: {project.get('id')})."]
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


# --- upload_files -----------------------------------------------------------------------


def _refused_path(path: Path) -> str | None:
    """Why ``path`` may not be uploaded by an agent, or None.

    Credential stores, a home folder's hidden folders (``~/.config``, ``~/.local``...),
    hidden files (``.env``, ``.netrc``) and key files stay on the computer, whichever
    path names them: the given one and the one a symlink leads to. A hidden folder
    elsewhere (a checkout under ``~/work/.worktrees``) is an ordinary place.
    """
    home = Path.home()
    for candidate in (path, path.resolve()):
        for part in candidate.parts:
            if part in _SECRET_DIRECTORIES:
                return f"it is inside {part}, which holds credentials or settings"
        if candidate.name.startswith("."):
            return f"{candidate.name} is a hidden file"
        try:
            below_home = candidate.relative_to(home).parts
        except ValueError:
            below_home = ()
        if below_home and below_home[0].startswith("."):
            return f"~/{below_home[0]} holds settings and credentials"
        if any(fnmatch.fnmatch(candidate.name.lower(), pattern) for pattern in _SECRET_NAMES):
            return f"{candidate.name} looks like a key or credential file"
    return None


@dataclass
class _Item:
    path: Path
    root: Path | None
    size: int


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


#: Uploads this server has run, by id, for ``upload_status``.
_UPLOADS: dict[str, _Upload] = {}


def _gather(paths: list[str]) -> tuple[list[_Item], list[dict[str, Any]]]:
    """The files to send, and the paths refused or skipped, with why."""
    items: list[_Item] = []
    skipped: list[dict[str, Any]] = []
    for given in paths:
        path = Path(str(given)).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.exists():
            skipped.append({"path": str(given), "reason": "no such file or folder"})
            continue
        refused = _refused_path(path)
        if refused:
            skipped.append({"path": str(given), "reason": f"not uploaded: {refused}"})
            continue
        if path.is_dir():
            for file_path in iter_folder_files(path):
                refused = _refused_path(file_path)
                if refused:
                    skipped.append({"path": str(file_path), "reason": f"not uploaded: {refused}"})
                    continue
                items.append(_Item(file_path, path, file_path.stat().st_size))
        else:
            items.append(_Item(path, None, path.stat().st_size))
    kept = []
    for item in items:
        if item.size == 0:
            skipped.append({"path": str(item.path), "reason": "empty file"})
        else:
            kept.append(item)
    return kept, skipped


def _file_flow(upload: _Upload, item: _Item, folders: dict[Path, str | None], children: dict):
    """One file's upload, into its folder (recreated from a folder upload's tree)."""
    target = upload.folder_id
    if item.root is not None:
        relative_dir = Path(item.root.name) / item.path.parent.relative_to(item.root)
        target = yield from _ensure_folder(relative_dir, folders, upload.project_id, children)
    return (
        yield from upload_flow(
            item.path,
            project_id=upload.project_id,
            folder_id=target,
            ingest=upload.ingest,
            relative_path=upload_relative_path(item.path, item.root),
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
        return await _resume(resume_upload_id, paths, project_name)
    items, skipped = _gather(paths)
    if len(items) > MAX_UPLOAD_FILES:
        return _error(
            "too_many_files",
            f"{len(items)} files is more than one call uploads ({MAX_UPLOAD_FILES}).",
            "Upload subfolders one call at a time, or ask the person to run "
            f"`beakr upload PATH --project {project_id}` in a terminal.",
        )
    if not items:
        return _error(
            "nothing_to_upload",
            "None of the paths is a file that can be uploaded.",
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
        total_bytes=sum(item.size for item in items),
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


async def _resume(upload_id: str, paths: list[str], project_name: str) -> str | dict[str, Any]:
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
        result = await call_api(lambda sdk: sdk.files.resume_upload(upload_id, path))
    except BeakrUploadInterrupted as exc:
        return _error(
            "upload_interrupted",
            str(exc),
            f"Retry with resume_upload_id={exc.upload_id!r} and the same path.",
            upload_id=exc.upload_id,
        )
    artifact = (result or {}).get("artifact") or {}
    return f"Finished uploading {path.name} to {project_name} (file {artifact.get('id')})."


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
        lines.append(f"- {Path(row['path']).name}: file {row['file_id']}")
    if len(upload.sent) > 50:
        lines.append(f"- ... and {len(upload.sent) - 50} more")
    for row in upload.failed[:20]:
        text = f"- Failed: {row['path']}: {row.get('message')}"
        if row.get("upload_id"):
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
_SERVER: FastMCP | None = None

PROJECT_TOOLS = (
    create_project,
    set_project_profile,
    upload_files,
    upload_status,
    add_project_member,
)


def register(server: FastMCP) -> None:
    """Register this module's tools on ``server``."""
    from mcp.types import ToolAnnotations

    from beakr_cli import tool_surface

    global _SERVER
    _SERVER = server
    for tool in PROJECT_TOOLS:
        name = tool.__name__
        server.tool(
            **tool_surface.tool_kwargs(name),
            annotations=ToolAnnotations(openWorldHint=True, **ANNOTATIONS[name]),
        )(tool)
