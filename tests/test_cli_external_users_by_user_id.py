"""External users are addressed by user_id, never by external_id alone.

Root cause: an external_id is unique only within the service account that
created the user, so two backends in one organization may each have a
"customer-42". A command that took an external_id to name one user could act
on another backend's user. Every per-user command (show, edit, rm, minting a
token for one) takes the user_id; `ls --external-id` stays as the caller-scoped
way to find it.
"""

from __future__ import annotations

import json
import re

import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.main import app

API = "https://api.test"
ORG = "org-1"
USER_ID = "5b1f6c2e-8d3a-4e7b-9c1d-2a3b4c5d6e7f"


@pytest.fixture(autouse=True)
def api(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")
    monkeypatch.setenv("BEAKR_ORG_ID", ORG)


@pytest.mark.parametrize(
    "argv",
    [
        ["external-users", "show", "customer-42"],
        ["external-users", "edit", "customer-42", "--name", "Ada"],
        ["external-users", "rm", "customer-42", "--yes"],
        ["end-user-tokens", "mint", "--scope", "kb:read", "--external-user", "customer-42"],
    ],
)
def test_an_external_id_is_refused_before_any_request(argv):
    with respx.mock(assert_all_called=False) as mock:
        anything = mock.route().respond(200, json={})
        result = CliRunner().invoke(app, argv)
    assert result.exit_code == 2, result.output
    # The usage error is boxed and wrapped; compare its words.
    text = " ".join(re.sub(r"[│╭╮╰╯─]", " ", result.output).split())
    assert "user_id" in text
    assert "beakr external-users ls --external-id customer-42" in text
    assert not anything.called


@respx.mock
def test_show_reads_the_user_by_its_user_id():
    route = respx.get(f"{API}/v1/organizations/{ORG}/external-users/{USER_ID}").respond(
        200, json={"user_id": USER_ID, "external_id": "customer-42", "name": "Ada"}
    )
    result = CliRunner().invoke(app, ["external-users", "show", USER_ID, "--json"])
    assert result.exit_code == 0, result.output
    assert route.called
    assert json.loads(result.output)["user_id"] == USER_ID


@respx.mock
def test_finding_by_external_id_is_the_callers_list_filter():
    route = respx.get(f"{API}/v1/organizations/{ORG}/external-users").respond(
        200,
        json={
            "data": [{"user_id": USER_ID, "external_id": "customer-42"}],
            "next_cursor": None,
            "has_more": False,
        },
    )
    result = CliRunner().invoke(
        app, ["external-users", "ls", "--external-id", "customer-42", "--json"]
    )
    assert result.exit_code == 0, result.output
    assert route.calls.last.request.url.params["external_id"] == "customer-42"
