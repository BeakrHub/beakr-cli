"""Every allowlisted unlisted operation is sent by the CLI's private client.

The SDK's contract coverage (beakr-python ``tests/test_contract_coverage.py``)
proves every documented operation has a method; this is its counterpart for
``beakr_cli._private_api``: for each operation on ``operations.allowlist`` the
mapping names the private-client call that serves it, the test runs that call
against a mocked transport, and asserts the request it sent is that operation.
``tests/test_private_api_allowlist.py`` keeps the allowlist equal to the call
sites; together they mean every listed operation is both used and exercised.
"""

from __future__ import annotations

import re
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from beakr_cli._private_api import PrivateBeakr
from beakr_cli._private_api._generated.operations import OPERATIONS

BASE = "https://api.test"
ORG = "org1"

# (allowlisted operation, private-client call). Path params use fixed sample ids.
MAPPING: list[tuple[str, Callable[[PrivateBeakr], Any]]] = [
    ("GET /v1/knowledge/wiki/work-board", lambda c: c.knowledge.work_board(project_id="p1")),
    ("GET /v1/artifacts/{}/versions", lambda c: c.files.versions("a1")),
    ("GET /v1/organizations/{}/audit", lambda c: c.org.audit.list(org_id=ORG)),
    ("GET /v1/auth/config", lambda c: c.auth.config()),
    ("GET /v1/outputs/{}", lambda c: c.outputs.get("c1")),
    (
        "GET /v1/outputs/{}/export/{}",
        lambda c: c.outputs.export("c1", "docx", Path(tempfile.mkdtemp()) / "out.docx"),
    ),
    ("GET /v1/outputs/{}/revisions", lambda c: c.outputs.revisions("c1")),
    ("GET /v1/outputs/{}/revisions/{}/content", lambda c: c.outputs.revision_content("c1", 2)),
    ("GET /v1/conversations/{}/freshness", lambda c: c.conversations.freshness("cv1")),
    ("GET /v1/conversations/settings", lambda c: c.conversations.get_settings()),
    (
        "PUT /v1/conversations/settings",
        lambda c: c.conversations.update_settings(full_text_clients=["codex"]),
    ),
    ("GET /v1/integrations/hosted/connectors/{}/scope", lambda c: c.integrations.get_scope("k1")),
    (
        "PUT /v1/integrations/hosted/connectors/{}/scope",
        lambda c: c.integrations.set_scope("k1", "all"),
    ),
    ("GET /v1/integrations/{}/coverage", lambda c: c.integrations.coverage("k1")),
    ("POST /v1/integrations/{}/sync", lambda c: c.integrations.sync("k1")),
    ("GET /v1/interview/requests", lambda c: c.interviews.requests()),
    (
        "POST /v1/interview/requests",
        lambda c: c.interviews.send_request("a@example.com", goals="g", questions=["q"]),
    ),
    (
        "POST /v1/interview/sendables/maker",
        lambda c: c.interviews.draft_sendable("Ask what changed in March."),
    ),
    ("GET /v1/interview/sessions", lambda c: c.interviews.sessions()),
    ("GET /v1/interview/sessions/{}", lambda c: c.interviews.session("x1")),
    ("GET /v1/invitations", lambda c: c.invitations.list()),
    ("POST /v1/invitations", lambda c: c.invitations.create("ada@example.com")),
    ("DELETE /v1/invitations/{}", lambda c: c.invitations.revoke("i1")),
    (
        "POST /v1/knowledge/profiles",
        lambda c: c.profile.create_profile(project_id="p1", goals=["Map it"]),
    ),
    ("GET /v1/knowledge/profiles/org", lambda c: c.profile.org()),
    ("GET /v1/knowledge/profiles/project/{}", lambda c: c.profile.project("p1")),
    ("GET /v1/knowledge/stats", lambda c: c.knowledge.pages.stats()),
    ("GET /v1/knowledge/wiki/graph", lambda c: c.knowledge.pages.graph()),
    ("GET /v1/knowledge/wiki/pages/{}/backlinks", lambda c: c.knowledge.pages.backlinks("pg1")),
    ("GET /v1/knowledge/wiki/pages/{}/blame", lambda c: c.knowledge.pages.blame("pg1")),
    (
        "GET /v1/knowledge/wiki/pages/{}/section-provenance",
        lambda c: c.knowledge.pages.section_provenance("pg1"),
    ),
    ("GET /v1/knowledge/conflicts", lambda c: c.knowledge.conflicts()),
    ("POST /v1/knowledge/runs/{}/undo", lambda c: c.knowledge.undo_run("run-1", dry_run=True)),
    ("GET /v1/changes", lambda c: c.knowledge.changes(agent="my-bot")),
    ("GET /v1/knowledge/wiki/timeline", lambda c: c.knowledge.pages.timeline()),
    ("GET /v1/me/memories", lambda c: c.memories.list()),
    ("GET /v1/me/profile", lambda c: c.profile.claims()),
    ("DELETE /v1/me/profile/claims/{}", lambda c: c.profile.forget("pc1")),
    (
        "PATCH /v1/me/profile",
        lambda c: c.profile.apply([{"claim_id": "pc1", "action": "confirm"}]),
    ),
    (
        "PATCH /v1/me/profile/claims/{}",
        lambda c: c.profile.edit("pc1", content="Leads assays"),
    ),
    ("DELETE /v1/me/memories/{}", lambda c: c.memories.delete("m1")),
    ("GET /v1/organizations/{}/auth-policy", lambda c: c.org.policy.get(org_id=ORG)),
    ("PUT /v1/organizations/{}/auth-policy", lambda c: c.org.policy.set(org_id=ORG)),
    ("GET /v1/organizations/{}/credentials", lambda c: c.org.credentials.list(org_id=ORG)),
    (
        "DELETE /v1/organizations/{}/credentials/{}/{}",
        lambda c: c.org.credentials.revoke("api_key", "cr1", org_id=ORG),
    ),
    ("GET /v1/trace/{}/{}", lambda c: c.trace.get("page", "pg1")),
    ("GET /v1/usage", lambda c: c.usage.get()),
    ("GET /v1/workflows/configs", lambda c: c.workflows.configs()),
    ("POST /v1/workflows/configs", lambda c: c.workflows.create_config("tpl1", name="Weekly")),
    ("PATCH /v1/workflows/configs/{}", lambda c: c.workflows.schedule("wc1", "0 9 * * 1")),
    ("POST /v1/workflows/configs/{}/run", lambda c: c.workflows.run_config("wc1")),
    ("GET /v1/workflows/configs/{}/runs", lambda c: c.workflows.config_runs("wc1")),
    ("GET /v1/workflows/runs", lambda c: c.workflows.runs()),
    ("GET /v1/workflows/runs/{}", lambda c: c.workflows.get_run("wr1")),
    ("POST /v1/workflows/schedule/preview", lambda c: c.workflows.preview_schedule("0 9 * * 1")),
    ("GET /v1/workflows/templates", lambda c: c.workflows.templates()),
    (
        "POST /v1/workflows/ai/generate",
        lambda c: c.workflows.generate_template("Summarize each assay run."),
    ),
    (
        "POST /v1/workflows/ai/refine-template",
        lambda c: c.workflows.refine_template({"name": "x"}, "Rename it."),
    ),
]


def _normalize(path: str) -> str:
    return re.sub(r"\{[^}]*\}", "{}", path)


def _matches(operation: str, method: str, path: str) -> bool:
    want_method, template = operation.split(" ", 1)
    pattern = "^" + re.escape(template).replace(r"\{\}", "[^/]+") + "$"
    return method == want_method and re.match(pattern, path) is not None


def _respond(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/stream") or re.fullmatch(r"/v1/runs/[^/]+/events", path):
        body = 'id: 1\ndata: {"type": "stream_complete", "data": {}}\n\n'
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
    payload = {
        "id": "x1",
        "org_id": ORG,
        "status": "ok",
        "data": [],
        "has_more": False,
        "content": [{"type": "text", "text": "ok"}],
        "outcome": "success",
        "issuer": "https://issuer.test",
        "client_id": "cli",
        "status_url": "/v1/project-imports/im1",
        "current_version_id": "v1",
        "url": "https://storage.test/object",
        "key": "uploads/k",
    }
    return httpx.Response(200, json=payload)


def test_every_allowlisted_operation_has_a_mapped_call() -> None:
    allowlisted = {f"{method} {_normalize(path)}" for method, path in OPERATIONS.values()}
    mapped = [operation for operation, _call in MAPPING]
    assert len(mapped) == len(set(mapped)), "two calls map one operation"
    assert sorted(allowlisted ^ set(mapped)) == []


@pytest.mark.parametrize(("operation", "call"), MAPPING, ids=[m[0] for m in MAPPING])
def test_private_call_sends_its_operation(operation, call) -> None:
    client = PrivateBeakr(api_key="bk_live_test", base_url=BASE)
    client._sleep = lambda _seconds: None
    try:
        with respx.mock(base_url=BASE, assert_all_called=False) as router:
            route = router.route().mock(side_effect=_respond)
            call(client)
    finally:
        client.close()
    sent = [(r.request.method, r.request.url.path) for r in route.calls]
    assert any(_matches(operation, method, path) for method, path in sent), (operation, sent)
