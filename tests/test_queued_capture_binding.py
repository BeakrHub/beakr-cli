"""A queued automatic capture stays bound to what its consent was given to (audit C073).

Root cause: ``flush_queue`` only asked whether capture was on in the session's
directory today, so rebinding that directory to project B and opting B in
authorized an upload of a payload captured for project A.
"""

from __future__ import annotations

import pytest
import respx

from beakr_cli import compounding, hook_queue
from beakr_cli.project_binding import write_binding
from beakr_cli.sdk import get_sdk

API = "https://api.test"
IDENTITY = compounding.HookIdentity(origin=API, principal="u1")


def _setup(tmp_path, monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_queue_binding")
    repo = tmp_path / "repo"
    repo.mkdir()
    return repo


def _queue(repo, *, recorded=None, body_project="proj-a", with_binding=True):
    entry = {
        "key": "s1",
        "identity": IDENTITY.as_dict(),
        "cwd": str(repo),
        "body": {"client": "claude-code", "session_id": "s1", "messages": []},
    }
    if body_project:
        entry["body"]["project_id"] = body_project
    if with_binding:
        entry["binding"] = recorded or {"project_id": "proj-a", "base_url": API, "org_id": "org-1"}
    return hook_queue.enqueue(entry)


def _flush():
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(f"{API}/v1/conversations/import").respond(
            json={"conversation_id": "c1", "created": True, "stored": "summary"}
        )
        with get_sdk() as sdk:
            compounding.flush_queue(sdk, IDENTITY)
        return route.called


def _bind(repo, project="proj-a", base_url=API, org_id="org-1", capture=True):
    write_binding(
        repo, {"project_id": project, "base_url": base_url, "org_id": org_id, "capture": capture}
    )


@pytest.fixture(autouse=True)
def _org_applies(monkeypatch):
    # The binding's org must apply to the credential for capture to count as on;
    # these tests vary the recorded org, so make every org apply.
    monkeypatch.setattr("beakr_cli.project_binding.binding_applies", lambda binding: True)


def test_a_matching_project_still_uploads(tmp_path, monkeypatch):
    repo = _setup(tmp_path, monkeypatch)
    _bind(repo)
    path = _queue(repo)
    assert _flush() is True
    assert not path.exists()


@pytest.mark.parametrize(
    "rebind",
    [
        {"project": "proj-b"},
        {"base_url": "https://other.test"},
        {"org_id": "org-2"},
    ],
    ids=["other-project", "other-api", "other-org"],
)
def test_a_rebound_directory_does_not_carry_the_old_payload(tmp_path, monkeypatch, rebind):
    repo = _setup(tmp_path, monkeypatch)
    _bind(repo, **rebind)
    path = _queue(repo)
    assert _flush() is False
    assert not path.exists()


def test_an_entry_naming_no_project_is_dropped(tmp_path, monkeypatch):
    repo = _setup(tmp_path, monkeypatch)
    _bind(repo)
    path = _queue(repo, body_project=None, with_binding=False)
    assert _flush() is False
    assert not path.exists()


def test_a_body_naming_another_project_than_recorded_is_dropped(tmp_path, monkeypatch):
    repo = _setup(tmp_path, monkeypatch)
    _bind(repo)
    path = _queue(repo, body_project="proj-z")
    assert _flush() is False
    assert not path.exists()


def test_the_capture_records_its_binding(tmp_path, monkeypatch):
    repo = _setup(tmp_path, monkeypatch)
    _bind(repo)
    assert compounding._binding_identity(str(repo)) == {
        "project_id": "proj-a",
        "base_url": API,
        "org_id": "org-1",
    }
