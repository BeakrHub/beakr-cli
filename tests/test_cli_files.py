"""``beakr upload`` and ``beakr files``."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.main import app
from beakr_cli.project_binding import write_binding
from tests.upload_routes import mock_resumable_uploads

API = "https://api.test"
S3 = "https://s3.test/bucket"


@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


@respx.mock
def test_upload_file_runs_the_resumable_flow(tmp_path):
    # Every upload uses the documented /v1/uploads route (a small file is one part).
    routes = mock_resumable_uploads(API)
    path = tmp_path / "notes.md"
    path.write_text("hello")

    result = CliRunner().invoke(app, ["upload", str(path), "--project", "p1", "--json"])

    assert result.exit_code == 0, result.output
    created = routes["created"][0]
    assert created["path"] == "notes.md"
    assert created["content_length"] == 5
    assert created["target_project_id"] == "p1"
    assert created["content_hash"] == hashlib.sha256(b"hello").hexdigest()
    put = routes["put"].calls[0].request
    assert put.content == b"hello"
    # The presigned URL is the authorization; the bearer token must not leak to S3.
    assert "authorization" not in put.headers
    assert json.loads(result.stdout)[0]["artifact"]["id"] == "art-notes.md"


@respx.mock
def test_upload_uses_the_bound_project_by_default(tmp_path):
    routes = mock_resumable_uploads(API)
    write_binding(Path.cwd(), {"project_id": "p-bound", "name": "Bound"})
    path = tmp_path / "a.txt"
    path.write_text("x")

    result = CliRunner().invoke(app, ["upload", str(path)])

    assert result.exit_code == 0, result.output
    assert routes["created"][0]["target_project_id"] == "p-bound"


@respx.mock
def test_upload_folder_mirrors_subdirectories(tmp_path):
    routes = mock_resumable_uploads(API)
    root = tmp_path / "docs"
    (root / "sub").mkdir(parents=True)
    (root / "top.txt").write_text("1")
    (root / "sub" / "deep.txt").write_text("2")
    (root / ".hidden").write_text("skip")

    result = CliRunner().invoke(app, ["upload", str(root), "--project", "p1", "--json"])

    assert result.exit_code == 0, result.output
    assert routes["complete"].call_count == 2
    # The uploaded directory becomes a folder of its own, and its tree goes under it.
    assert [json.loads(c.request.content) for c in routes["folder"].calls] == [
        {"name": "docs", "project_id": "p1"},
        {"name": "sub", "project_id": "p1", "parent_folder_id": "fold-docs"},
    ]
    assert [c.get("parent_folder_id") for c in routes["created"]] == ["fold-sub", "fold-docs"]
    paths = [row["path"] for row in json.loads(result.stdout)]
    assert [Path(p).name for p in paths] == ["deep.txt", "top.txt"]


@respx.mock
def test_upload_folder_reuses_a_folder_name_already_in_the_project(tmp_path):
    # Root cause: upload_folder always created folders, so a second matter with a
    # "Closing" subfolder failed with "A folder named 'Closing' already exists".
    routes = mock_resumable_uploads(
        API,
        folders=[
            {"id": "fold-1005", "name": "1005-00001", "parent_folder_id": None},
            {"id": "fold-closing", "name": "Closing", "parent_folder_id": "fold-1005"},
        ],
    )
    root = tmp_path / "1005-00001"
    (root / "Closing").mkdir(parents=True)
    (root / "Closing" / "memo.txt").write_text("1")

    result = CliRunner().invoke(app, ["upload", str(root), "--project", "p1", "--json"])

    assert result.exit_code == 0, result.output
    assert routes["folder"].call_count == 0
    assert [c.get("parent_folder_id") for c in routes["created"]] == ["fold-closing"]


@respx.mock
def test_upload_no_knowledge_keeps_files_out_of_knowledge(tmp_path):
    routes = mock_resumable_uploads(API)
    single = tmp_path / "one.txt"
    single.write_text("1")
    root = tmp_path / "docs"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "two.txt").write_text("2")

    result = CliRunner().invoke(
        app, ["upload", str(single), str(root), "--project", "p1", "--no-knowledge"]
    )

    assert result.exit_code == 0, result.output
    assert [body["ingest_to_wiki"] for body in routes["created"]] == [False, False]


@respx.mock
def test_upload_sends_files_to_knowledge_by_default(tmp_path):
    routes = mock_resumable_uploads(API)
    path = tmp_path / "a.txt"
    path.write_text("x")

    assert CliRunner().invoke(app, ["upload", str(path), "--project", "p1"]).exit_code == 0
    assert routes["created"][0]["ingest_to_wiki"] is True


@respx.mock
def test_upload_resume_finishes_an_interrupted_large_upload(tmp_path):
    path = tmp_path / "big.bin"
    path.write_bytes(b"abcdef")
    state = {
        "id": "u9",
        "status": "open",
        "project_id": "p1",
        "content_length": 6,
        "part_size": 6,
        "part_count": 1,
        "uploaded_parts": [],
    }
    respx.get(f"{API}/v1/uploads/u9").respond(200, json=state)
    respx.post(f"{API}/v1/uploads/u9/parts").respond(
        200, json={"parts": [{"part_number": 1, "url": f"{S3}/u9?partNumber=1&sig=x"}]}
    )
    put = respx.put(url__startswith=S3).respond(200, headers={"ETag": '"e1"'})
    complete = respx.post(f"{API}/v1/uploads/u9/complete").respond(
        200, json={"artifact": {"id": "a9"}, "job_id": "j9", "upload": state, "folders": []}
    )

    result = CliRunner().invoke(app, ["upload", str(path), "--resume", "u9", "--json"])

    assert result.exit_code == 0, result.output
    assert put.calls[0].request.content == b"abcdef"
    assert json.loads(complete.calls[0].request.content)["parts"] == [
        {"part_number": 1, "etag": '"e1"'}
    ]
    assert json.loads(result.stdout)[0]["artifact"]["id"] == "a9"


def test_upload_resume_takes_exactly_one_file(tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    first.write_text("1")
    second.write_text("2")
    result = CliRunner().invoke(app, ["upload", str(first), str(second), "--resume", "u1"])
    assert result.exit_code == 2


def test_upload_folder_without_project_is_a_usage_error(tmp_path):
    (tmp_path / "d").mkdir()

    assert CliRunner().invoke(app, ["upload", str(tmp_path / "d")]).exit_code == 2


def test_upload_missing_path_exits_not_found(tmp_path):
    assert CliRunner().invoke(app, ["upload", str(tmp_path / "missing.pdf")]).exit_code == 4


@respx.mock
def test_files_ls_passes_filters():
    route = respx.get(f"{API}/v1/artifacts").respond(
        json={"data": [{"id": "a1", "title": "Doc"}], "next_cursor": None, "has_more": False}
    )

    result = CliRunner().invoke(
        app, ["files", "ls", "--project", "p1", "--search", "doc", "--limit", "5", "--json"]
    )

    assert result.exit_code == 0, result.output
    params = route.calls[0].request.url.params
    assert (params["project_id"], params["search"], params["limit"]) == ("p1", "doc", "5")
    assert json.loads(result.stdout) == [{"id": "a1", "title": "Doc"}]


@respx.mock
def test_files_download_writes_original_bytes_into_a_directory(tmp_path):
    # One request signs any reference (an id here; a source_ref works the same).
    signed = respx.post(f"{API}/v1/artifacts/download-url").respond(
        json={"url": f"{S3}/report.pdf?sig=1", "expires_in": 120, "filename": "report.pdf"}
    )
    respx.get(url__startswith=f"{S3}/report.pdf").respond(content=b"%PDF-1.7")

    result = CliRunner().invoke(app, ["files", "download", "a1", "--out", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert signed.called
    assert (tmp_path / "report.pdf").read_bytes() == b"%PDF-1.7"


@respx.mock
def test_files_download_into_a_new_directory_keeps_the_trailing_slash(tmp_path, monkeypatch):
    # Regression (sandbox QA 2026-11-01): `beakr files download ID -o newdir/`
    # wrote the PDF as a FILE named `newdir`. Typer turned the option into a
    # Path, which drops the trailing "/", so the SDK could no longer tell that
    # a directory to create was meant (beakr_sdk.resources._destination).
    respx.post(f"{API}/v1/artifacts/download-url").respond(
        json={"url": f"{S3}/report.pdf?sig=1", "expires_in": 120, "filename": "report.pdf"}
    )
    respx.get(url__startswith=f"{S3}/report.pdf").respond(content=b"%PDF-1.7")
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["files", "download", "a1", "--out", "newdir/"])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "newdir").is_dir()
    assert (tmp_path / "newdir" / "report.pdf").read_bytes() == b"%PDF-1.7"


@respx.mock
def test_files_show_not_found_exits_4():
    respx.get(f"{API}/v1/artifacts/missing").respond(404, json={"detail": "Not found"})

    assert CliRunner().invoke(app, ["files", "show", "missing"]).exit_code == 4


def test_files_rm_refuses_without_yes_when_not_interactive():
    assert CliRunner().invoke(app, ["files", "rm", "a1"]).exit_code == 2


@respx.mock
def test_files_rm_with_yes_deletes():
    route = respx.delete(f"{API}/v1/artifacts/a1").respond(204)

    assert CliRunner().invoke(app, ["files", "rm", "a1", "--yes"]).exit_code == 0
    assert route.called


def _completed_relative_paths(routes: dict) -> list[str | None]:
    return [
        json.loads(call.request.content).get("relative_path") for call in routes["complete"].calls
    ]


@respx.mock
@pytest.mark.parametrize("flags", [[], ["--no-mirror"]])
def test_upload_folder_records_each_files_path_from_the_folders_parent(
    tmp_path, monkeypatch, flags
):
    # Regression: a folder upload dropped the folder's own name, and --no-mirror
    # dropped every directory, so the path Knowledge reads showed for a cited
    # file (location.display_path) was not one the user could follow back.
    routes = mock_resumable_uploads(API)
    (tmp_path / "docs" / "guides").mkdir(parents=True)
    (tmp_path / "docs" / "guides" / "a.md").write_text("a")
    (tmp_path / "docs" / "top.md").write_text("t")
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(app, ["upload", "docs", "--project", "p1", *flags])

    assert result.exit_code == 0, result.output
    assert all("relative_path" not in body for body in routes["created"])
    assert _completed_relative_paths(routes) == ["docs/guides/a.md", "docs/top.md"]


@respx.mock
def test_upload_file_records_the_relative_path_typed(tmp_path, monkeypatch):
    routes = mock_resumable_uploads(API)
    (tmp_path / "deals" / "acme").mkdir(parents=True)
    (tmp_path / "deals" / "acme" / "memo.txt").write_text("m")
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(
        app,
        [
            "upload",
            "deals/acme/memo.txt",
            str(tmp_path / "deals" / "acme" / "memo.txt"),
            "--project",
            "p1",
        ],
    )

    assert result.exit_code == 0, result.output
    assert _completed_relative_paths(routes) == ["deals/acme/memo.txt", None]


class _PlacementSDK:
    """A stand-in SDK whose upload methods record their keyword arguments.

    ``supports`` says whether its methods take ``on_existing`` / ``written_by_agent``,
    as a beakr-sdk that has them does; the CLI checks before uploading.
    """

    def __init__(self, *, supports: bool, result: dict | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        answer = result or {"artifact": {"id": "a1"}, "job_id": "j1", "result": "created"}
        outer = self

        if supports:

            class Files:
                def upload(
                    self,
                    path,
                    *,
                    project_id=None,
                    folder_id=None,
                    ingest=True,
                    on_existing=None,
                    written_by_agent=None,
                    version_of=None,
                ):
                    recorded = {"on_existing": on_existing, "written_by_agent": written_by_agent}
                    if version_of is not None:
                        recorded["version_of"] = version_of
                    outer.calls.append(("upload", recorded))
                    return dict(answer)

                def upload_folder(
                    self,
                    path,
                    *,
                    project_id,
                    folder_id=None,
                    mirror_folders=True,
                    ingest=True,
                    on_existing=None,
                    written_by_agent=None,
                ):
                    outer.calls.append(
                        (
                            "upload_folder",
                            {"on_existing": on_existing, "written_by_agent": written_by_agent},
                        )
                    )
                    return [{"path": str(path), **answer}]

                def resume_upload(
                    self, upload_id, path, *, on_existing=None, written_by_agent=None
                ):
                    outer.calls.append(
                        (
                            "resume_upload",
                            {"on_existing": on_existing, "written_by_agent": written_by_agent},
                        )
                    )
                    return dict(answer)

        else:

            class Files:  # type: ignore[no-redef]
                def upload(self, path, *, project_id=None, folder_id=None, ingest=True):
                    outer.calls.append(("upload", {}))
                    return dict(answer)

        self.files = Files()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _with_sdk(monkeypatch, sdk: _PlacementSDK) -> None:
    from beakr_cli.commands import files as files_commands

    monkeypatch.setattr(files_commands, "get_sdk", lambda **_kwargs: sdk)


def test_upload_on_existing_and_written_by_agent_reach_the_sdk(tmp_path, monkeypatch):
    sdk = _PlacementSDK(supports=True)
    _with_sdk(monkeypatch, sdk)
    path = tmp_path / "summary.md"
    path.write_text("x")
    folder = tmp_path / "docs"
    folder.mkdir()
    (folder / "a.md").write_text("a")

    result = CliRunner().invoke(
        app,
        [
            "upload",
            str(path),
            str(folder),
            "-P",
            "p1",
            "--on-existing",
            "NEW",
            "--written-by-agent",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert sdk.calls == [
        ("upload", {"on_existing": "new", "written_by_agent": True}),
        ("upload_folder", {"on_existing": "new", "written_by_agent": True}),
    ]


def test_resume_passes_them_through(tmp_path, monkeypatch):
    sdk = _PlacementSDK(supports=True)
    _with_sdk(monkeypatch, sdk)
    path = tmp_path / "big.pdf"
    path.write_bytes(b"%PDF")

    result = CliRunner().invoke(
        app, ["upload", str(path), "--resume", "u1", "--on-existing", "version"]
    )

    assert result.exit_code == 0, result.output
    assert sdk.calls == [("resume_upload", {"on_existing": "version", "written_by_agent": None})]


def test_without_the_flags_nothing_is_added_so_the_server_default_applies(tmp_path, monkeypatch):
    sdk = _PlacementSDK(supports=False)
    _with_sdk(monkeypatch, sdk)
    path = tmp_path / "a.md"
    path.write_text("x")

    result = CliRunner().invoke(app, ["upload", str(path), "-P", "p1"])

    assert result.exit_code == 0, result.output
    assert sdk.calls == [("upload", {})]


def test_the_flags_with_an_sdk_that_lacks_them_stop_before_uploading(tmp_path, monkeypatch):
    sdk = _PlacementSDK(supports=False)
    _with_sdk(monkeypatch, sdk)
    path = tmp_path / "a.md"
    path.write_text("x")

    result = CliRunner().invoke(app, ["upload", str(path), "-P", "p1", "--written-by-agent"])

    assert result.exit_code == 2
    assert "--written-by-agent needs a newer beakr-sdk" in result.output
    assert sdk.calls == []


def test_on_existing_takes_version_or_new_only(tmp_path):
    path = tmp_path / "a.md"
    path.write_text("x")

    result = CliRunner().invoke(app, ["upload", str(path), "-P", "p1", "--on-existing", "replace"])

    assert result.exit_code == 2
    assert "version" in result.output and "new" in result.output


def test_a_versioned_upload_says_so(tmp_path, monkeypatch):
    from beakr_cli.commands.files import _placement_note as files_note

    sdk = _PlacementSDK(
        supports=True,
        result={"artifact": {"id": "a1"}, "job_id": "j2", "result": "versioned", "version_no": 3},
    )
    _with_sdk(monkeypatch, sdk)
    path = tmp_path / "q3.pdf"
    path.write_bytes(b"%PDF")

    shown = CliRunner().invoke(app, ["upload", str(path), "-P", "p1"])
    assert shown.exit_code == 0, shown.output
    assert "\tjob=j2\tresult=versioned\tversion=3" in shown.output
    assert files_note({"result": "versioned", "version_no": 3}) == " (new version 3)"
    assert files_note({"result": "unchanged"}).startswith(" (unchanged")
    assert files_note({"result": "created"}) == ""


@respx.mock
def test_the_default_upload_sends_neither_option_on_complete(tmp_path):
    routes = mock_resumable_uploads(API)
    path = tmp_path / "notes.md"
    path.write_text("hello")

    result = CliRunner().invoke(app, ["upload", str(path), "--project", "p1"])

    assert result.exit_code == 0, result.output
    body = json.loads(routes["complete"].calls[0].request.content)
    assert "on_existing" not in body and "written_by_agent" not in body


def test_version_of_names_the_file_and_takes_one_file(tmp_path, monkeypatch):
    sdk = _PlacementSDK(supports=True)
    _with_sdk(monkeypatch, sdk)
    path = tmp_path / "q3-final.pdf"
    path.write_bytes(b"%PDF")

    result = CliRunner().invoke(app, ["upload", str(path), "-P", "p1", "--version-of", "a7"])

    assert result.exit_code == 0, result.output
    assert sdk.calls == [
        ("upload", {"on_existing": None, "written_by_agent": None, "version_of": "a7"})
    ]
    two = CliRunner().invoke(
        app, ["upload", str(path), str(path), "-P", "p1", "--version-of", "a7"]
    )
    assert two.exit_code == 2
    assert "exactly one file" in two.output
