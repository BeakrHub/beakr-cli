"""Setting a project up from an agent: create_project, set_project_profile, upload_files,
upload_status and add_project_member (``beakr_cli.mcp_tools_projects``).

Root cause guarded: ``beakr mcp`` could read Knowledge and propose changes, but not
upload a file, create a project or add a colleague, so an agent sent the person to the
web app for each. Each promise here has a test:

- the tools are listed with the shared surface's description, in the discovery tier;
- a created project's result says to set the profile up first, entity types first, and
  a name already in use is not created twice;
- a first profile needs entity types; a later one changes only the fields given;
- a refusal every later file would meet too (no access) stops the upload;
- hidden files, home settings folders and key files are never uploaded;
- an upload that outlasts the call goes on in this process and upload_status reports it;
- an organization member is added by email; anyone else is invited to the project,
  never to the organization.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
import respx

from beakr_cli import mcp_server, mcp_tools_platform, mcp_tools_projects, tool_surface
from tests.upload_routes import mock_resumable_uploads

API = "https://api.test"
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
    mcp_tools_platform._resolved_projects.clear()
    mcp_tools_projects._UPLOADS.clear()
    yield
    mcp_tools_platform._resolved_projects.clear()
    mcp_tools_projects._UPLOADS.clear()


def _projects(*ids: str) -> dict:
    return {"data": [{"id": p, "name": NAMES[p]} for p in ids], "has_more": False}


def _file(directory: Path, name: str, text: str = "lot LB-400 passed") -> Path:
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


# --- Listing -----------------------------------------------------------------------------


async def test_the_tools_are_listed_in_the_discovery_tier_with_the_shared_text() -> None:
    listed = {tool.name: tool for tool in await mcp_server.mcp.list_tools()}
    key = tool_surface.document()["tier_meta_key"]
    for name in TOOLS:
        assert listed[name].description == tool_surface.description(name)
        assert listed[name].meta == {key: "discovery"}
        assert name in tool_surface.document()["discovery"]
    # One description serves both servers, so it says what each of them takes.
    upload = listed["upload_files"].description
    assert "`paths`" in upload and "`files`" in upload and "beakr upload PATH" in upload
    assert set(listed["upload_files"].inputSchema["properties"]) == {
        "project",
        "paths",
        "folder_id",
        "knowledge",
        "resume_upload_id",
    }
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
    assert "requires a Knowledge profile on every new project" in text
    assert "entity types first" in text and "set_project_profile" in text


@respx.mock
async def test_a_name_already_in_use_is_not_created_twice() -> None:
    respx.get(f"{API}/v1/projects").respond(json=_projects(A))
    created = respx.post(f"{API}/v1/projects")

    result = await mcp_tools_projects.create_project("helios")

    assert result["error_code"] == "project_exists" and A in result["message"]
    assert not created.called


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
async def test_a_refusal_every_file_would_meet_stops_the_upload(tmp_path: Path) -> None:
    first = _file(tmp_path, "a.pdf")
    second = _file(tmp_path, "b.pdf")
    respx.get(f"{API}/v1/projects/{NEW}").respond(json={"id": NEW, "name": "Stability"})
    respx.get(f"{API}/v1/projects").respond(json=_projects(NEW))
    create = respx.post(f"{API}/v1/uploads").respond(
        403,
        json={
            "error": {
                "code": "forbidden",
                "message": "You need to be a member of 'Stability' to add files to it.",
                "fix": "Ask one of the project's owners to add you.",
            }
        },
    )

    text = await mcp_tools_projects.upload_files(NEW, paths=[str(first), str(second)])

    assert create.call_count == 1  # the second file would meet the same refusal
    assert "Uploaded 0 of 2 files" in text
    assert "Stopped: You need to be a member of 'Stability'" in text
    assert "Ask one of the project's owners to add you." in text


@respx.mock
async def test_one_file_that_fails_does_not_stop_the_rest(tmp_path: Path) -> None:
    bad = _file(tmp_path, "notes.xyz")
    good = _file(tmp_path, "memo.pdf")
    respx.get(f"{API}/v1/projects/{A}").respond(json={"id": A, "name": "Helios"})
    respx.get(f"{API}/v1/projects").respond(json=_projects(A))
    routes = mock_resumable_uploads(API)
    accept = routes["create"].side_effect

    def create(request: httpx.Request) -> httpx.Response:
        if json.loads(request.content)["path"] == "notes.xyz":
            return httpx.Response(
                400,
                json={"error": {"code": "unsupported_file_type", "message": "Not supported."}},
            )
        return accept(request)

    routes["create"].mock(side_effect=create)

    text = await mcp_tools_projects.upload_files(A, paths=[str(bad), str(good)])

    assert "Uploaded 1 of 2 files" in text and "file art-memo.pdf" in text
    assert "Failed:" in text and "notes.xyz: Not supported." in text
    assert "Stopped" not in text


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
