"""Setting a project up from an agent: create_project, set_project_profile, upload_files,
upload_status and add_project_member (``beakr_cli.mcp_tools_projects``).

Root cause guarded: ``beakr mcp`` could read Knowledge and propose changes, but not
upload a file, create a project or add a colleague, so an agent on a self-hosted full
deployment (where the web app has no chat) sent the person to the web app for each.
Each promise here has a test:

- a session uploads only into projects it may write, checked before the disk is read;
- a project the session creates is one it may write, and the result says to set the
  profile up first, entity types first;
- a first profile needs entity types; a later one changes only the fields given;
- a refused upload (the profile the organization requires) stops and says what to do;
- hidden files, home settings folders and key files are never uploaded;
- an upload that outlasts the call goes on in this process and upload_status reports it;
- an organization member is added by email; anyone else is invited to the project,
  never to the organization.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
import respx

from beakr_cli import (
    mcp_server,
    mcp_tools_platform,
    mcp_tools_projects,
    scope_session,
    tool_surface,
)
from beakr_cli.scope_session import McpSession, TokenState
from tests.upload_routes import mock_resumable_uploads

API = "https://api.test"
SID = "7c1e2d3f-4a5b-4c6d-8e7f-9a0b1c2d3e4f"
A = "4dd25154-0d80-44ab-b4c2-167319bfacb4"
B = "8b2f6c10-3d4e-4f5a-9b6c-7d8e9f0a1b2c"
NEW = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
NAMES = {A: "Helios", B: "Assays", NEW: "Stability"}
TOOLS = (
    "create_project",
    "set_project_profile",
    "upload_files",
    "upload_status",
    "add_project_member",
)


@pytest.fixture(autouse=True)
def api(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_person_key_for_tests")
    monkeypatch.delenv(scope_session.MCP_SESSIONS_ENV, raising=False)
    scope_session.ACTIVE = None
    mcp_tools_platform._resolved_projects.clear()
    yield
    scope_session.ACTIVE = None
    mcp_tools_platform._resolved_projects.clear()


def _projects(*ids: str) -> dict:
    return {"data": [{"id": p, "name": NAMES[p]} for p in ids], "has_more": False}


def _session(read=(A, B), write=(A,)) -> dict:
    return {
        "id": SID,
        "status": "active",
        "read_projects": [{"id": p, "name": NAMES[p]} for p in read],
        "write_projects": [{"id": p, "name": NAMES[p]} for p in write],
        "picker_url": f"https://app.test/sessions/{SID}",
    }


def _hold(session: dict) -> McpSession:
    held = McpSession(folder=None)
    expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    held.state = TokenState(SID, "bk_st_first", expires)
    held.session = session
    scope_session.ACTIVE = held
    return held


def _file(directory: Path, name: str, text: str = "lot LB-400 passed") -> Path:
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


# --- Listing -----------------------------------------------------------------------------


async def test_the_tools_are_listed_in_the_discovery_tier_with_their_own_text() -> None:
    listed = {tool.name: tool for tool in await mcp_server.mcp.list_tools()}
    key = tool_surface.document()["tier_meta_key"]
    for name in TOOLS:
        assert listed[name].description == mcp_tools_projects.DESCRIPTIONS[name]
        assert listed[name].meta == {key: "discovery"}
    assert listed["upload_status"].annotations.readOnlyHint is True
    assert listed["upload_files"].annotations.readOnlyHint is False
    # No tool here invites anyone into the organization.
    assert not any("invite_to_org" in name or "organization_invite" in name for name in listed)
    assert "create_project, then its Knowledge profile" in mcp_server.instructions()
    assert "create_project" not in mcp_server.instructions({"create_project"})


# --- create_project ----------------------------------------------------------------------


@respx.mock
async def test_a_created_project_points_to_the_profile_first() -> None:
    respx.get(f"{API}/v1/projects").respond(json=_projects(A))
    created = respx.post(f"{API}/v1/projects").respond(
        201, json={"id": NEW, "name": "Stability", "requires_knowledge_profile": True}
    )

    text = await mcp_tools_projects.create_project("Stability", description="Stability studies")

    assert json.loads(created.calls.last.request.content) == {
        "name": "Stability",
        "description": "Stability studies",
    }
    assert f"Created project 'Stability' (id: {NEW})" in text
    assert "requires a Knowledge profile" in text
    assert "entity types first" in text and "set_project_profile" in text


@respx.mock
async def test_a_name_already_in_use_is_not_created_twice() -> None:
    respx.get(f"{API}/v1/projects").respond(json=_projects(A))
    created = respx.post(f"{API}/v1/projects")

    result = await mcp_tools_projects.create_project("helios")

    assert result["error_code"] == "project_exists" and A in result["message"]
    assert not created.called


@respx.mock
async def test_a_session_may_write_the_project_it_creates() -> None:
    _hold(_session())
    respx.get(f"{API}/v1/projects").respond(json=_projects(A, B))
    created = respx.post(f"{API}/v1/projects").respond(
        201, json={"id": NEW, "name": "Stability", "requires_knowledge_profile": False}
    )
    respx.get(f"{API}/v1/sessions/current").respond(json=_session(read=(A, B, NEW), write=(A, NEW)))

    text = await mcp_tools_projects.create_project("Stability")

    assert created.calls.last.request.headers["authorization"] == "Bearer bk_st_first"
    assert "This session may read and write it." in text
    assert "set_project_profile" in text


@respx.mock
async def test_an_engine_that_refuses_sessions_a_project_says_what_the_person_does() -> None:
    _hold(_session())
    respx.get(f"{API}/v1/projects").respond(json=_projects(A, B))
    respx.post(f"{API}/v1/projects").respond(
        403,
        json={
            "error": {
                "code": "insufficient_scope",
                "message": "This credential is restricted to specific projects and cannot "
                "call an endpoint that is not addressed by a single project.",
            }
        },
    )

    result = await mcp_tools_projects.create_project("Stability")

    assert result["error_code"] == "session_create_refused"
    assert f"https://app.test/sessions/{SID}" in result["fix"]


# --- set_project_profile -----------------------------------------------------------------


def _no_profile() -> httpx.Response:
    return httpx.Response(
        404,
        json={"detail": {"code": "knowledge_profile_not_found", "message": "No profile yet."}},
    )


@respx.mock
async def test_a_first_profile_starts_with_entity_types() -> None:
    respx.get(f"{API}/v1/projects/{NEW}").respond(json={"id": NEW, "name": "Stability"})
    respx.get(f"{API}/v1/projects/{NEW}/knowledge-profile").mock(return_value=_no_profile())
    put = respx.put(f"{API}/v1/projects/{NEW}/knowledge-profile")

    refused = await mcp_tools_projects.set_project_profile(NEW, goals=["Track shelf life"])

    assert refused["error_code"] == "entity_types_required"
    assert not put.called


@respx.mock
async def test_a_first_profile_is_written_whole_and_reads_back_entity_types_first() -> None:
    respx.get(f"{API}/v1/projects/{NEW}").respond(
        json={"id": NEW, "name": "Stability", "requires_knowledge_profile": False}
    )
    respx.get(f"{API}/v1/projects/{NEW}/knowledge-profile").mock(return_value=_no_profile())
    put = respx.put(f"{API}/v1/projects/{NEW}/knowledge-profile").respond(
        json={"entity_types": ["lot", "compound"], "goals": ["Track shelf life"]}
    )

    text = await mcp_tools_projects.set_project_profile(
        NEW,
        entity_types=["lot", "compound"],
        goals=["Track shelf life"],
        extraction_guidance="A lot is one manufactured batch, e.g. LB-400.",
    )

    assert json.loads(put.calls.last.request.content) == {
        "entity_types": ["lot", "compound"],
        "goals": ["Track shelf life"],
        "extraction_guidance": "A lot is one manufactured batch, e.g. LB-400.",
    }
    lines = text.splitlines()
    assert lines[0] == "Saved the Knowledge profile of Stability."
    assert lines[1] == "Entity types: lot, compound"
    assert "Files can be uploaded now" in text


@respx.mock
async def test_a_saved_profile_changes_only_the_fields_given() -> None:
    respx.get(f"{API}/v1/projects/{NEW}").respond(json={"id": NEW, "name": "Stability"})
    respx.get(f"{API}/v1/projects/{NEW}/knowledge-profile").respond(json={"entity_types": ["lot"]})
    patch = respx.patch(f"{API}/v1/projects/{NEW}/knowledge-profile").respond(
        json={"entity_types": ["lot"], "focus_areas": ["Degradation"]}
    )

    text = await mcp_tools_projects.set_project_profile(NEW, focus_areas=["Degradation"])

    assert json.loads(patch.calls.last.request.content) == {"focus_areas": ["Degradation"]}
    assert text.startswith("Updated the Knowledge profile of Stability.")


# --- upload_files ------------------------------------------------------------------------


@respx.mock
async def test_files_and_folders_upload_with_their_tree(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _file(tmp_path, "memo.pdf")
    _file(tmp_path, "assays/2026/lb-400.csv")
    _file(tmp_path, "assays/.hidden-notes.txt")
    respx.get(f"{API}/v1/projects/{A}").respond(json={"id": A, "name": "Helios"})
    respx.get(f"{API}/v1/projects").respond(json=_projects(A))
    routes = mock_resumable_uploads(API)

    text = await mcp_tools_projects.upload_files(A, paths=["memo.pdf", str(tmp_path / "assays")])

    created = routes["created"]
    assert [body["target_project_id"] for body in created] == [A, A]
    assert [f["name"] for f in routes["folders"]] == ["assays", "2026"]
    assert "Uploaded 2 of 2 files" in text and "to Helios" in text
    assert ".hidden-notes.txt" not in text  # a folder's hidden files are not even listed
    assert "Knowledge compiles from them" in text


@pytest.mark.parametrize(
    "relative",
    [".env", "keys/server.pem", "deploy/id_ed25519", ".config/gh/hosts.yml", "repo/.git/config"],
)
@respx.mock
async def test_hidden_and_credential_files_are_never_uploaded(
    tmp_path: Path, monkeypatch, relative: str
) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    secret = _file(tmp_path, relative, "SECRET=1")
    respx.get(f"{API}/v1/projects/{A}").respond(json={"id": A, "name": "Helios"})
    respx.get(f"{API}/v1/projects").respond(json=_projects(A))
    routes = mock_resumable_uploads(API)

    result = await mcp_tools_projects.upload_files(A, paths=[str(secret)])

    assert result["error_code"] == "nothing_to_upload"
    assert "not uploaded" in result["skipped"][0]["reason"]
    assert not routes["create"].called


@respx.mock
async def test_a_symlink_to_a_credential_is_refused(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    key = _file(tmp_path, ".ssh/id_rsa", "KEY")
    link = tmp_path / "docs" / "notes.txt"
    link.parent.mkdir()
    link.symlink_to(key)
    assert mcp_tools_projects._refused_path(link) is not None
    # A hidden folder that is not a home settings folder is an ordinary place.
    work = _file(tmp_path, "work/.worktrees/repo/report.pdf")
    assert mcp_tools_projects._refused_path(work) is None


@respx.mock
async def test_a_session_uploads_only_into_projects_it_may_write(tmp_path: Path) -> None:
    _hold(_session(read=(A, B), write=(A,)))
    memo = _file(tmp_path, "memo.pdf")
    respx.get(f"{API}/v1/projects/{B}").respond(json={"id": B, "name": "Assays"})
    respx.get(f"{API}/v1/projects").respond(json=_projects(A, B))
    respx.get(f"{API}/v1/sessions/current").respond(json=_session(read=(A, B), write=(A,)))
    routes = mock_resumable_uploads(API)

    result = await mcp_tools_projects.upload_files(B, paths=[str(memo)])

    assert result["error_code"] == scope_session.WRITE_SCOPE
    assert "It may write: Helios" in result["message"]
    assert result["picker_url"] == f"https://app.test/sessions/{SID}"
    assert not routes["create"].called


@respx.mock
async def test_a_project_that_owes_its_profile_stops_the_upload(tmp_path: Path) -> None:
    first = _file(tmp_path, "a.pdf")
    second = _file(tmp_path, "b.pdf")
    respx.get(f"{API}/v1/projects/{NEW}").respond(json={"id": NEW, "name": "Stability"})
    respx.get(f"{API}/v1/projects").respond(json=_projects(NEW))
    # The engine's own 409 (beakr/main.py, project_profile_required_response): the
    # legacy ``detail`` beside the ``error`` envelope, its fix worded for MCP callers.
    message = (
        "Project 'Stability' needs a Knowledge profile before files or Knowledge can be "
        "added to it: the organization requires one on every new project."
    )
    create = respx.post(f"{API}/v1/uploads").respond(
        409,
        json={
            "detail": {
                "code": "project_profile_required",
                "message": message,
                "project_id": NEW,
                "project_name": "Stability",
            },
            "error": {
                "code": "project_profile_required",
                "project_id": NEW,
                "project_name": "Stability",
                "message": message,
                "fix": "Ask the person to set up this project's Knowledge profile first: in "
                "the web app (open the project and choose Set up the profile) or with `beakr "
                "projects profile setup --project <project>` in a terminal. Then retry.",
                "details": {"project_id": NEW, "project_name": "Stability"},
                "request_id": None,
            },
        },
    )

    text = await mcp_tools_projects.upload_files(NEW, paths=[str(first), str(second)])

    assert create.call_count == 1  # the second file met the same refusal, so it was not sent
    assert "Uploaded 0 of 2 files" in text
    assert "Stopped: Project 'Stability' needs a Knowledge profile" in text
    assert "entity types first, save it with set_project_profile" in text


@respx.mock
async def test_an_upload_that_outlasts_the_call_carries_on(tmp_path: Path, monkeypatch) -> None:
    memo = _file(tmp_path, "memo.pdf")
    respx.get(f"{API}/v1/projects/{A}").respond(json={"id": A, "name": "Helios"})
    respx.get(f"{API}/v1/projects").respond(json=_projects(A))
    routes = mock_resumable_uploads(API)
    monkeypatch.setattr(mcp_tools_projects, "UPLOAD_WAIT_SECONDS", 0.0)

    text = await mcp_tools_projects.upload_files(A, paths=[str(memo)])

    assert "It carries on in the background" in text
    upload_id = text.split("upload_id '")[1].split("'")[0]
    upload = mcp_tools_projects._UPLOADS[upload_id]
    await asyncio.wait_for(upload.task, timeout=10)
    status = await mcp_tools_projects.upload_status(upload_id)
    assert "Uploaded 1 of 1 files" in status and "file art-memo.pdf" in status
    assert routes["create"].call_count == 1
    missing = await mcp_tools_projects.upload_status("up_nothing")
    assert missing["error_code"] == "upload_not_found"


# --- add_project_member ------------------------------------------------------------------

ORG = "99999999-8888-7777-6666-555555555555"
JANE = "22222222-3333-4444-5555-666666666666"


def _org_routes(members: list[dict]) -> None:
    respx.get(f"{API}/v1/me/organization").respond(json={"id": ORG, "name": "Octant"})
    respx.get(f"{API}/v1/organizations/{ORG}/members").respond(
        json={"data": members, "has_more": False, "next_cursor": None}
    )


@respx.mock
async def test_an_organization_member_is_added_by_email() -> None:
    respx.get(f"{API}/v1/projects/{A}").respond(json={"id": A, "name": "Helios"})
    _org_routes([{"user_id": JANE, "email": "jane@octant.bio", "display_name": "Jane Roe"}])
    added = respx.post(f"{API}/v1/projects/{A}/members").respond(
        201, json={"principal_id": JANE, "role": "viewer"}
    )

    text = await mcp_tools_projects.add_project_member(A, "Jane@Octant.bio", role="viewer")

    assert json.loads(added.calls.last.request.content) == {
        "principal_id": JANE,
        "principal_type": "user",
        "role": "viewer",
    }
    assert text == "Added Jane Roe <jane@octant.bio> to the project as viewer."


@respx.mock
async def test_someone_outside_the_organization_is_invited_to_the_project() -> None:
    respx.get(f"{API}/v1/projects/{A}").respond(json={"id": A, "name": "Helios"})
    _org_routes([])
    invited = respx.post(f"{API}/v1/projects/{A}/invite").respond(201, json={"id": "inv-1"})
    org_invite = respx.post(f"{API}/v1/invitations")

    text = await mcp_tools_projects.add_project_member(A, "sam@octant.bio")

    assert json.loads(invited.calls.last.request.content)["invitee_email"] == "sam@octant.bio"
    assert "Invited sam@octant.bio" in text and "first sign in" in text
    assert not org_invite.called


@respx.mock
async def test_another_domain_needs_an_organization_admin() -> None:
    respx.get(f"{API}/v1/projects/{A}").respond(json={"id": A, "name": "Helios"})
    _org_routes([])
    respx.post(f"{API}/v1/projects/{A}/invite").respond(
        400,
        json={
            "detail": "The domain @evil.example is not configured for auto-join in this "
            "organization. Use Share (access grant) instead for external collaborators."
        },
    )

    result = await mcp_tools_projects.add_project_member(A, "x@evil.example")

    assert result["error_code"] == "invitation_domain_refused"
    assert "Use Share" not in result["message"]
    assert "does not send organization invitations" in result["fix"]


@respx.mock
async def test_a_name_two_members_share_is_refused() -> None:
    respx.get(f"{API}/v1/projects/{A}").respond(json={"id": A, "name": "Helios"})
    _org_routes(
        [
            {"user_id": JANE, "email": "jane@octant.bio", "display_name": "J. Roe"},
            {"user_id": ORG, "email": "joe@octant.bio", "display_name": "J. Roe"},
        ]
    )
    added = respx.post(f"{API}/v1/projects/{A}/members")

    result = await mcp_tools_projects.add_project_member(A, "j. roe")

    assert result["error_code"] == "person_ambiguous"
    assert not added.called


def _review(holds: bool) -> dict:
    policy = {
        "agent_proposals_need_a_person": False,
        "agent_uploads_need_a_person": holds,
        "capture_auto_approve_allowed": False,
    }
    return {
        "available": True,
        "project_id": A,
        "effective": policy,
        "org": policy,
        "project_override": None,
    }


@pytest.mark.real_upload_review
@pytest.mark.parametrize("holds", [True, False])
@respx.mock
async def test_an_upload_held_for_a_person_says_so(tmp_path: Path, monkeypatch, holds) -> None:
    """rc.9 QA: the reply promised Knowledge "within a few minutes" for a held file."""
    monkeypatch.chdir(tmp_path)
    _file(tmp_path, "memo.pdf")
    respx.get(f"{API}/v1/projects/{A}").respond(json={"id": A, "name": "Helios"})
    respx.get(f"{API}/v1/projects").respond(json=_projects(A))
    # The file says nothing yet (no hold recorded): the policy decides.
    respx.get(url__regex=rf"{API}/v1/artifacts/art-[^/]+$").respond(json={"id": "art"})
    policy = respx.get(f"{API}/v1/knowledge/agent-review").respond(json=_review(holds))
    mock_resumable_uploads(API)

    text = await mcp_tools_projects.upload_files(A, paths=["memo.pdf"])

    assert policy.calls.last.request.url.params["project_id"] == A
    if holds:
        assert (
            "Stored. A person must approve it before it's added to Knowledge (Files → the "
            "file → Approve, or `beakr files approve art-memo.pdf`)." in text
        )
        assert "Knowledge compiles from them" not in text
    else:
        assert "Knowledge compiles from them" in text and "must approve" not in text


# --- rc.10 bug 4: an auto-lock session's first write ---------------------------------------


def _auto_lock(state: str, touched=()) -> dict:
    """An auto_lock session as the API reports it: no write list while open."""
    locked = touched[0] if state == "locked" else None
    return {
        "id": SID,
        "status": "active",
        "mode": "auto_lock",
        "lock_state": state,
        "agent_session": None,
        "write_project": {"id": locked, "name": NAMES[locked]} if locked else None,
        "read_projects": [{"id": p, "name": NAMES[p]} for p in touched],
        "write_projects": [{"id": locked, "name": NAMES[locked]}] if locked else [],
        "touched_projects": [{"id": p, "name": NAMES[p]} for p in touched],
        "picker_url": f"https://app.test/sessions/{SID}",
    }


async def _upload_into(tmp_path: Path, session: dict, target: str):
    _hold(session)
    memo = _file(tmp_path, "memo.pdf")
    respx.get(f"{API}/v1/projects/{target}").respond(json={"id": target, "name": NAMES[target]})
    respx.get(f"{API}/v1/projects").respond(json=_projects(A, B))
    respx.get(f"{API}/v1/sessions/current").respond(json=session)
    routes = mock_resumable_uploads(API)
    result = await mcp_tools_projects.upload_files(target, paths=[str(memo)])
    return result, routes


@respx.mock
async def test_an_open_auto_lock_session_uploads_its_first_write(tmp_path: Path) -> None:
    """rc.9 QA: refused as "It may write: none", though the server locks on that write."""
    result, routes = await _upload_into(tmp_path, _auto_lock("open"), A)

    assert isinstance(result, str), result
    assert "Uploaded 1 of 1 files" in result
    assert routes["create"].called


@respx.mock
async def test_a_locked_session_uploads_into_its_own_project(tmp_path: Path) -> None:
    result, routes = await _upload_into(tmp_path, _auto_lock("locked", touched=(A,)), A)

    assert isinstance(result, str), result
    assert routes["create"].called


@respx.mock
async def test_a_locked_session_refuses_another_project_without_the_picker(tmp_path: Path) -> None:
    result, routes = await _upload_into(tmp_path, _auto_lock("locked", touched=(A,)), B)

    assert result["error_code"] == scope_session.LOCKED_TO_PROJECT
    assert "reads and writes Helios, so it cannot write Assays" in result["message"]
    assert "picker_url" not in result and "/sessions/" not in str(result.get("fix"))
    assert not routes["create"].called


@respx.mock
async def test_a_read_only_session_refuses_every_upload(tmp_path: Path) -> None:
    result, routes = await _upload_into(tmp_path, _auto_lock("read_only", touched=(A, B)), A)

    assert result["error_code"] == scope_session.READ_ONLY
    assert "read Helios, Assays, so it is read-only" in result["message"]
    assert not routes["create"].called


@pytest.mark.parametrize(
    ("state", "touched", "said"),
    [
        ("open", (), "its first write locks the session to that project"),
        ("locked", (A,), "Locked to Helios"),
    ],
)
@respx.mock
async def test_create_project_says_what_an_auto_lock_session_may_do(state, touched, said) -> None:
    session = _auto_lock(state, touched=touched)
    _hold(session)
    respx.get(f"{API}/v1/projects").respond(json=_projects(A))
    respx.post(f"{API}/v1/projects").respond(201, json={"id": NEW, "name": "Stability"})
    respx.get(f"{API}/v1/sessions/current").respond(json=session)

    text = await mcp_tools_projects.create_project("Stability")

    assert said in text
    assert "ask the person to add it" not in text


@pytest.mark.real_upload_review
@respx.mock
async def test_an_upload_held_in_an_agent_session_says_so(tmp_path: Path, monkeypatch) -> None:
    """rc.10 QA: inside a session the policy read is refused, so the reply fell back to
    "Knowledge compiles ...": the file's own knowledge_status now says it waits."""
    monkeypatch.chdir(tmp_path)
    _hold(_session(read=(A,), write=(A,)))
    _file(tmp_path, "memo.pdf")
    respx.get(f"{API}/v1/projects/{A}").respond(json={"id": A, "name": "Helios"})
    respx.get(f"{API}/v1/projects").respond(json=_projects(A))
    respx.get(f"{API}/v1/sessions/current").respond(json=_session(read=(A,), write=(A,)))
    status = respx.get(f"{API}/v1/artifacts/art-memo.pdf").respond(
        json={"id": "art-memo.pdf", "knowledge_status": "waiting_for_person"}
    )
    policy = respx.get(f"{API}/v1/knowledge/agent-review").respond(
        403, json={"error": {"code": "insufficient_scope", "message": "Not for a session."}}
    )
    mock_resumable_uploads(API)

    text = await mcp_tools_projects.upload_files(A, paths=["memo.pdf"])

    assert status.calls.last.request.headers["authorization"] == "Bearer bk_st_first"
    assert not policy.called
    assert "Stored. A person must approve it before it's added to Knowledge" in text
    assert "beakr files approve art-memo.pdf" in text
    assert "Knowledge compiles from them" not in text
