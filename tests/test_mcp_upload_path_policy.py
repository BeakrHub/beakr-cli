"""What the ``upload_files`` MCP tool refuses to send, and that ordinary uploads still go.

ROOT CAUSE (security review 2026-11-13): the tool takes filesystem paths from an
agent and sent whatever they named. ``/etc/passwd``, a keychain and a browser's
cookie store were accepted; the secret-name list missed ``prod.env``,
``terraform.tfstate``, ``kubeconfig`` and ``service-account.json``; a symlink
inside a folder was followed out of it; and one call had no size limit. The
rules are in ``beakr_cli/upload_path_policy.py``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import respx

from beakr_cli import mcp_tools_platform, mcp_tools_projects, upload_path_policy
from beakr_cli.upload_path_policy import outside_link, refused_path
from tests.upload_routes import mock_resumable_uploads

API = "https://api.test"
A = "4dd25154-0d80-44ab-b4c2-167319bfacb4"


@pytest.fixture(autouse=True)
def api(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_person_key_for_tests")
    mcp_tools_platform._resolved_projects.clear()
    mcp_tools_projects._UPLOADS.clear()
    yield
    mcp_tools_platform._resolved_projects.clear()
    mcp_tools_projects._UPLOADS.clear()


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    """A home folder with a project checked out in it, which is the working directory."""
    home = tmp_path / "person"
    (home / "work" / "helios").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(home / "work" / "helios")
    return home


def _file(path: Path, text: str = "lot LB-400 passed") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _sent(text: str) -> list[str]:
    """The names of the files an upload's answer says it sent."""
    return sorted(
        line[2:].split(": file ")[0]
        for line in text.splitlines()
        if line.startswith("- ") and ": file " in line
    )


def _project_routes() -> dict:
    respx.get(f"{API}/v1/projects/{A}").respond(json={"id": A, "name": "Helios"})
    respx.get(f"{API}/v1/projects").respond(
        json={"data": [{"id": A, "name": "Helios"}], "has_more": False}
    )
    return mock_resumable_uploads(API)


# --- Places -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/etc/passwd",
        "/private/etc/hosts",
        "/var/db/dslocal/nodes/Default/users/root.plist",
        "/private/var/root/notes.txt",
        "/proc/self/environ",
        "/sys/kernel/notes",
        "/Library/Keychains/System.keychain",
    ],
)
def test_a_system_location_is_refused(path: str) -> None:
    reason = refused_path(Path(path), named=True)

    assert reason is not None and "belongs to the system" in reason


@pytest.mark.parametrize(
    "relative",
    [
        "Library/Keychains/login.keychain-db",
        "Library/Application Support/Google/Chrome/Default/Cookies",
        "Library/Application Support/Firefox/Profiles/x.default/logins.json",
        "Library/Cookies/Cookies.binarycookies",
        "Library/Safari/History.db",
        "AppData/Local/Google/Chrome/User Data/Default/Login Data",
        ".ssh/config",
        ".aws/config",
        ".gnupg/pubring.kbx",
        ".config/gcloud/application_default_credentials.json",
        ".kube/config",
        ".mozilla/firefox/x.default/logins.json",
    ],
)
def test_a_credential_store_under_the_home_folder_is_refused(home: Path, relative: str) -> None:
    # Even named exactly, and even when the home folder is the working directory.
    path = _file(home / relative)
    os.chdir(home)

    assert refused_path(path, named=True) is not None
    assert refused_path(Path(relative), named=True) is not None


@pytest.mark.parametrize(
    "relative",
    [
        "Documents/Helios/memo.pdf",
        "Desktop/plate-map.xlsx",
        "Downloads/protocol v2.docx",
        "work/helios/docs/readme.md",
        "work/.worktrees/repo/report.pdf",
        "Library/Mobile Documents/com~apple~CloudDocs/Helios/memo.pdf",
        "Library/CloudStorage/GoogleDrive-a@b.c/My Drive/memo.pdf",
    ],
)
def test_a_document_where_people_keep_documents_is_not_refused(home: Path, relative: str) -> None:
    path = _file(home / relative)

    assert refused_path(path, named=True) is None
    assert refused_path(path, named=False) is None


# --- Names ------------------------------------------------------------------------------

CREDENTIAL_NAMES = [
    "prod.env",
    "terraform.tfstate",
    "terraform.tfstate.backup",
    "prod.tfvars",
    "kubeconfig",
    "kubeconfig.yaml",
    "service-account.json",
    "helios-service-account-7f3a.json",
    "service_account.json",
    "aws-credentials.csv",
    "credentials",
    "client_secret_123.json",
    "secrets.yaml",
    "server.pem",
    "signing.key",
    "id_rsa",
    "id_ed25519.pub",
    "store.p12",
    "vault.kdbx",
]


@pytest.mark.parametrize("name", CREDENTIAL_NAMES)
def test_a_credential_shaped_name_goes_only_when_named_under_the_working_directory(
    home: Path, name: str
) -> None:
    inside = _file(Path.cwd() / "deploy" / name)
    elsewhere = _file(home / "Documents" / name)

    # Found on a folder walk, or named from somewhere else: it stays.
    assert "key or credential" in (refused_path(inside, named=False) or "")
    assert "key or credential" in (refused_path(elsewhere, named=True) or "")
    # Under the working directory and named exactly: the person's choice.
    assert refused_path(inside, named=True) is None
    assert refused_path(Path("deploy") / name, named=True) is None


@pytest.mark.parametrize("name", [".env", ".env.production", ".netrc", ".npmrc", ".pypirc"])
def test_a_hidden_credential_file_is_never_sent(home: Path, name: str) -> None:
    path = _file(Path.cwd() / name, "TOKEN=1")

    assert refused_path(path, named=True) is not None
    assert refused_path(path, named=False) is not None


@respx.mock
async def test_a_project_folder_uploads_without_its_credential_files(home: Path) -> None:
    project = Path.cwd()
    _file(project / "docs" / "protocol.md")
    _file(project / "data" / "plate.csv")
    for name in ("prod.env", "terraform.tfstate", "kubeconfig", "service-account.json"):
        _file(project / "deploy" / name, "SECRET")
    routes = _project_routes()

    text = await mcp_tools_projects.upload_files(A, paths=[str(project)])

    assert "Uploaded 2 of 2 files" in text
    assert _sent(text) == ["plate.csv", "protocol.md"]
    assert len(routes["created"]) == 2
    for name in ("prod.env", "terraform.tfstate", "kubeconfig", "service-account.json"):
        assert f"deploy/{name}: not uploaded: {name} looks like a key or credential file" in text


@respx.mock
async def test_a_named_document_from_documents_and_the_working_directory_uploads(
    home: Path,
) -> None:
    memo = _file(home / "Documents" / "memo.pdf")
    _file(Path.cwd() / "report.md")
    routes = _project_routes()

    text = await mcp_tools_projects.upload_files(A, paths=[str(memo), "report.md"])

    assert "Uploaded 2 of 2 files" in text
    assert _sent(text) == ["memo.pdf", "report.md"]
    assert len(routes["created"]) == 2


@respx.mock
async def test_each_refused_path_says_why(home: Path) -> None:
    _file(home / "Library" / "Keychains" / "login.keychain-db")
    _file(home / "Documents" / "prod.env")
    routes = _project_routes()

    result = await mcp_tools_projects.upload_files(
        A,
        paths=[
            "/etc/passwd",
            "~/Library/Keychains/login.keychain-db",
            str(home / "Documents" / "prod.env"),
        ],
    )

    assert result["error_code"] == "nothing_to_upload"
    reasons = {row["path"]: row["reason"] for row in result["skipped"]}
    assert "belongs to the system" in reasons["/etc/passwd"]
    assert "application data" in reasons["~/Library/Keychains/login.keychain-db"]
    assert "key or credential" in reasons[str(home / "Documents" / "prod.env")]
    assert not routes["create"].called


# --- Links ------------------------------------------------------------------------------


@respx.mock
async def test_a_link_out_of_the_folder_being_uploaded_is_not_followed(home: Path) -> None:
    project = Path.cwd()
    _file(project / "docs" / "protocol.md")
    outside = _file(home / "Documents" / "private" / "salaries.xlsx")
    (project / "docs" / "linked.xlsx").symlink_to(outside)
    (project / "docs" / "linked-folder").symlink_to(outside.parent, target_is_directory=True)
    # A link that stays inside the folder is part of it.
    (project / "docs" / "alias.md").symlink_to(project / "docs" / "protocol.md")
    routes = _project_routes()

    text = await mcp_tools_projects.upload_files(A, paths=[str(project / "docs")])

    assert _sent(text) == ["alias.md", "protocol.md"]
    assert len(routes["created"]) == 2
    assert "linked.xlsx: not uploaded: it is a link to a file outside docs" in text
    # A linked folder is not walked into, so nothing under it is sent or listed.
    assert "salaries.xlsx" not in text
    assert outside_link(project / "docs" / "alias.md", project / "docs") is None


# --- Size -------------------------------------------------------------------------------


@respx.mock
async def test_one_call_uploads_no_more_than_its_byte_limit(home: Path, monkeypatch) -> None:
    monkeypatch.setattr(mcp_tools_projects, "MAX_UPLOAD_BYTES", 100)
    project = Path.cwd()
    _file(project / "data" / "a.csv", "x" * 60)
    _file(project / "data" / "b.csv", "x" * 60)
    routes = _project_routes()

    result = await mcp_tools_projects.upload_files(A, paths=[str(project / "data")])

    assert result["error_code"] == "upload_too_large"
    assert "120 bytes in 2 files is more than one call uploads (100 bytes)" in result["message"]
    assert "fewer files or one subfolder a call" in result["fix"]
    assert f"beakr upload PATH --project {A}" in result["fix"]
    assert not routes["create"].called
    # Under the limit, the same folder goes.
    monkeypatch.setattr(mcp_tools_projects, "MAX_UPLOAD_BYTES", 120)
    assert "Uploaded 2 of 2 files" in await mcp_tools_projects.upload_files(
        A, paths=[str(project / "data")]
    )
    assert upload_path_policy.MAX_UPLOAD_BYTES >= 1024**3
