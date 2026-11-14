"""Paths ``upload_files`` is given directly: links, ``..`` and folders (sandbox run, 2026-11-14).

ROOT CAUSES:

* the outside-link rule covered only links found inside a folder being
  uploaded, so ``paths=["link-out.txt"]``, a link to a file elsewhere, sent that
  file under the link's name;
* ``../outside/secret.txt`` left the working folder and was sent;
* when nothing could be sent, the reply was a bare ``nothing_to_upload`` with no
  reason per path;
* ``sub/valve-note.txt`` landed at the project's root as ``valve-note.txt``,
  where a folder upload keeps each file's folders.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import respx

from beakr_cli import mcp_tools_platform, mcp_tools_projects
from beakr_cli.upload_path_policy import named_path_refusal
from tests.test_mcp_upload_path_policy import _file, _project_routes

API = "https://api.test"
A = "4dd25154-0d80-44ab-b4c2-167319bfacb4"


@pytest.fixture(autouse=True)
def api(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_person_key_for_tests")
    mcp_tools_platform._resolved_projects.clear()
    mcp_tools_projects._UPLOADS.clear()
    yield
    mcp_tools_projects._UPLOADS.clear()


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "person"
    (home / "work" / "helios").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(home / "work" / "helios")
    return home


@respx.mock
async def test_a_named_link_to_a_file_outside_its_folder_is_not_uploaded(home: Path) -> None:
    target = _file(home / "Documents" / "secret.txt")
    os.symlink(target, Path.cwd() / "link-out.txt")
    routes = _project_routes()

    result = await mcp_tools_projects.upload_files(A, paths=["link-out.txt"])

    assert result["error_code"] == "nothing_to_upload"
    # The reason is in the message itself, and names the file to upload instead.
    assert "link-out.txt: not uploaded: it is a link to" in result["message"]
    assert f"name it itself ({target})" in result["message"]
    assert not routes["create"].called


@respx.mock
async def test_a_named_link_within_its_own_folder_is_uploaded(home: Path) -> None:
    _file(Path.cwd() / "notes" / "pump.txt")
    os.symlink(Path.cwd() / "notes" / "pump.txt", Path.cwd() / "notes" / "latest.txt")
    routes = _project_routes()

    result = await mcp_tools_projects.upload_files(A, paths=["notes/latest.txt"])

    assert isinstance(result, str), result
    assert routes["create"].called


@respx.mock
async def test_a_relative_path_out_of_the_working_folder_and_the_home_folder_is_refused(
    home: Path, tmp_path: Path
) -> None:
    _file(tmp_path / "outside" / "secret.txt")
    os.chdir(tmp_path / "person")  # the home folder's parent holds `outside`
    os.chdir(home / "work" / "helios")
    routes = _project_routes()
    escaping = os.path.relpath(tmp_path / "outside" / "secret.txt", Path.cwd())
    assert escaping.startswith("..")

    result = await mcp_tools_projects.upload_files(A, paths=[escaping])

    assert result["error_code"] == "nothing_to_upload"
    assert "leads out of the working folder" in result["message"]
    assert not routes["create"].called


@respx.mock
async def test_a_relative_path_to_elsewhere_in_home_is_sent_and_named_by_its_real_path(
    home: Path,
) -> None:
    target = _file(home / "Documents" / "memo.txt")
    routes = _project_routes()

    result = await mcp_tools_projects.upload_files(A, paths=["../../Documents/memo.txt"])

    assert isinstance(result, str), result
    assert routes["create"].called
    # The reply says which file was read, not only the name it was sent under.
    assert f"(read from {target.resolve()})" in result or f"(read from {target})" in result


def test_the_rules_for_named_paths(home: Path, tmp_path: Path) -> None:
    cwd = Path.cwd()
    _file(cwd / "a.txt")
    assert named_path_refusal("a.txt", cwd / "a.txt", cwd) is None
    # An absolute path is not a `..` escape: the place rules decide it.
    _file(tmp_path / "elsewhere" / "b.txt")
    assert (
        named_path_refusal(
            str(tmp_path / "elsewhere" / "b.txt"), tmp_path / "elsewhere" / "b.txt", cwd
        )
        is None
    )


@respx.mock
async def test_nothing_uploadable_gives_each_paths_reason(home: Path) -> None:
    _file(Path.cwd() / "prod.env", "TOKEN=1")
    _project_routes()

    result = await mcp_tools_projects.upload_files(A, paths=["prod.env", "missing.txt"])

    assert result["error_code"] == "nothing_to_upload"
    assert (
        "prod.env: not uploaded: prod.env looks like a key or credential file"
        in (result["message"])
    )
    assert "missing.txt: no such file or folder" in result["message"]


@respx.mock
async def test_a_relative_path_inside_the_working_folder_keeps_its_folder(home: Path) -> None:
    _file(Path.cwd() / "pump-note.txt")
    _file(Path.cwd() / "sub" / "valve-note.txt")
    routes = _project_routes()

    result = await mcp_tools_projects.upload_files(A, paths=["pump-note.txt", "sub/valve-note.txt"])

    assert isinstance(result, str), result
    created = routes["created"]
    completed = [json.loads(call.request.content) for call in routes["complete"].calls]
    # The folder `sub` was created in the project and the file put in it, as a
    # folder upload of `sub` would; the root file stays at the root.
    assert routes["folders"] == [{"id": "fold-sub", "name": "sub", "parent_folder_id": None}]
    folder_of = {
        done["title"]: body.get("parent_folder_id") for done, body in zip(completed, created)
    }
    assert folder_of == {"pump-note.txt": None, "valve-note.txt": "fold-sub"}
    assert [done.get("relative_path") for done in completed] == [None, "sub/valve-note.txt"]
    assert "- valve-note.txt (in folder sub/): file " in result
