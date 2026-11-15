"""A project name resolves per shell session (the self-hosted line's rule, kept through
main's single resolver, ``project_ref.pick_project``).

A shell session's token reads only that session's projects, so the same name can
name another project, or none, in another session or outside any: what one session
resolved is never answered for another. Inside one session a name is looked up once.
(The "isn't one of this session's projects" refusal is in test_cli_session_ended.py.)
"""

from __future__ import annotations

import httpx
import pytest
import respx

from beakr_cli import project_ref, scope_session

API = "https://api.test"
A = "4dd25154-0d80-44ab-b4c2-167319bfacb4"
B = "56100fcd-8dcf-4844-81cd-f19852340430"


@pytest.fixture(autouse=True)
def api(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_person_key_for_tests")
    monkeypatch.setattr(project_ref, "_resolved", {})


def _page(project_id: str) -> httpx.Response:
    return httpx.Response(
        200, json={"data": [{"id": project_id, "name": "Helios"}], "has_more": False}
    )


@pytest.mark.real_project_refs
@respx.mock
def test_a_name_is_resolved_per_session_and_once_within_one(monkeypatch):
    listed = respx.get(f"{API}/v1/projects").mock(side_effect=[_page(A), _page(B), _page(A)])
    session = {"id": "s1"}
    monkeypatch.setattr(scope_session, "token_session_id", lambda: session["id"])
    # Which credential the listing goes out with is test_cli_session_ended's; here, the cache.
    monkeypatch.setattr(scope_session, "shell_token", lambda: None)

    assert project_ref.resolve_project_ref("Helios") == A
    assert project_ref.resolve_project_ref("Helios") == A  # cached within s1
    session["id"] = "s2"
    assert project_ref.resolve_project_ref("Helios") == B  # s2 reads its own projects
    session["id"] = None
    assert project_ref.resolve_project_ref("Helios") == A  # outside any session: asked again

    assert listed.call_count == 3
