"""A directory binding applies only to the API and organization it was made for.

Root cause of the regression this guards: ``resolve_project`` returned the bound
project for any credential, so a key for another organization (or a login on
another server) sent a project id it cannot see.
"""

from __future__ import annotations

from pathlib import Path

import respx

from beakr_cli.project_binding import resolve_project, write_binding

API = "https://api.test"


def _bind(**extra) -> None:
    write_binding(Path.cwd(), {"project_id": "p-bound", "name": "Bound", **extra})


@respx.mock
def test_a_binding_for_the_credential_s_org_applies(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_same")
    active = respx.get(f"{API}/v1/me/organization").respond(json={"id": "org-a"})
    _bind(base_url=API, org_id="org-a")
    assert resolve_project() == "p-bound"
    # The credential's org is cached: one lookup per credential, not per command.
    assert resolve_project() == "p-bound"
    assert active.call_count == 1


@respx.mock
def test_a_binding_is_ignored_under_a_key_for_another_org(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_other")
    respx.get(f"{API}/v1/me/organization").respond(json={"id": "org-b"})
    _bind(base_url=API, org_id="org-a")
    assert resolve_project() is None


def test_a_binding_is_ignored_on_another_api(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", "https://sandbox.test")
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_x")
    _bind(base_url=API, org_id="org-a")
    assert resolve_project() is None


@respx.mock
def test_an_unknown_org_keeps_the_binding(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_offline")
    respx.get(f"{API}/v1/me/organization").respond(503)
    _bind(base_url=API, org_id="org-a")
    assert resolve_project() == "p-bound"


def test_an_older_binding_without_an_org_still_applies(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_x")
    _bind()
    assert resolve_project() == "p-bound"
