"""Queued capture must remain authorized for the project in its stored payload."""

import pytest
import respx

from beakr_cli import compounding, hook_queue
from beakr_cli.project_binding import write_binding
from beakr_cli.sdk import get_sdk

API = "https://api.test"


@pytest.mark.parametrize("current_project", ["original", "replacement"])
def test_queue_does_not_reuse_new_project_consent_for_an_old_project(
    tmp_path, monkeypatch, current_project: str
) -> None:
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_queue_review")
    repo = tmp_path / "repo"
    repo.mkdir()
    write_binding(
        repo,
        {"project_id": current_project, "base_url": API, "org_id": None, "capture": True},
    )
    identity = compounding.HookIdentity(origin=API, principal="review-user")
    queued = hook_queue.enqueue(
        {
            "key": "review-session",
            "identity": identity.as_dict(),
            "cwd": str(repo),
            "body": {
                "client": "claude-code",
                "session_id": "review-session",
                "project_id": "original",
                "messages": [],
            },
        }
    )
    with respx.mock(assert_all_called=False) as mock:
        imported = mock.post(f"{API}/v1/conversations/import").respond(
            json={"conversation_id": "c-review", "created": True, "stored": "summary"}
        )
        with get_sdk() as sdk:
            assert compounding.flush_queue(sdk, identity)
        assert imported.called is (current_project == "original"), (
            "Consent for the replacement project must not authorize upload to the old project"
        )
    assert queued is not None and not queued.exists()
