"""Behaviour of the C039 parity methods beyond the request shape the coverage test checks.

``test_contract_coverage.py`` proves every contract operation has a method that
sends it; these tests pin the bodies, defaults and multi-request flows.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from .conftest import BASE


# The resources tested here include unlisted routes, reached through the
# private client (``beakr_cli._private_api.PrivateBeakr``) the CLI uses.
@pytest.fixture
def client(internal_client):
    return internal_client


@pytest.fixture
def async_client(async_internal_client):
    return async_internal_client


@respx.mock
def test_create_config_carries_skills_in_meta_and_saves_it(client) -> None:
    route = respx.post(f"{BASE}/v1/workflows/configs").respond(201, json={"id": "w1"})
    client.workflows.create_config(
        "tpl1",
        name="Weekly digest",
        skill_ids=["s1", "s2"],
        output_location_type="project",
        output_location_id="p1",
        meta={"mode": "agent"},
    )
    body = json.loads(route.calls.last.request.content)
    assert body["meta"] == {"mode": "agent", "skill_ids": ["s1", "s2"]}
    assert body["is_saved"] is True
    assert body["inputs"] == {}
    assert body["output_location_id"] == "p1"


@respx.mock
def test_schedule_turns_the_schedule_on(client) -> None:
    route = respx.patch(f"{BASE}/v1/workflows/configs/w1").respond(200, json={"id": "w1"})
    client.workflows.schedule("w1", "0 9 * * MON", timezone="UTC")
    assert json.loads(route.calls.last.request.content) == {
        "schedule_cron": "0 9 * * MON",
        "schedule_timezone": "UTC",
        "schedule_enabled": True,
    }


@respx.mock
def test_outputs_list_is_the_thread_output_listing(client) -> None:
    route = respx.get(f"{BASE}/v1/artifacts").respond(200, json={"items": []})
    client.outputs.list(project_id="p1", limit=5)
    params = route.calls.last.request.url.params
    assert params["thread_outputs"] == "true"
    assert params["project_id"] == "p1"
    assert params["limit"] == "5"


@respx.mock
def test_outputs_export_writes_the_rendered_file(client, tmp_path) -> None:
    route = respx.get(f"{BASE}/v1/outputs/c1/export/docx").respond(200, content=b"PK..")
    path = client.outputs.export("c1", "docx", tmp_path / "out" / "memo.docx", sources="none")
    assert path.read_bytes() == b"PK.."
    assert route.calls.last.request.url.params["sources"] == "none"


@respx.mock
async def test_async_outputs_export_writes_the_rendered_file(async_client, tmp_path) -> None:
    respx.get(f"{BASE}/v1/outputs/c1/export/pdf").respond(200, content=b"%PDF")
    path = await async_client.outputs.export("c1", "pdf", tmp_path / "memo.pdf")
    assert path.read_bytes() == b"%PDF"


@respx.mock
def test_set_scope_sends_items_only_when_restricted(client) -> None:
    path = f"{BASE}/v1/integrations/hosted/connectors/c1/scope"
    route = respx.put(path).respond(200, json={})
    client.integrations.set_scope("c1", "all")
    assert json.loads(route.calls.last.request.content) == {"mode": "all"}
    items = [{"id": "f1", "type": "folder", "name": "Contracts"}]
    client.integrations.set_scope("c1", "restricted", items=items)
    assert json.loads(route.calls.last.request.content) == {"mode": "restricted", "items": items}


@respx.mock
def test_skill_proposal_accept_passes_force(client) -> None:
    route = respx.post(f"{BASE}/v1/me/skills/proposals/sp1/accept").respond(200, json={})
    client.skills.proposals.accept("sp1", force=True)
    assert route.calls.last.request.url.params["force"] == "true"


@pytest.mark.parametrize("fmt", ["pdf", "docx", "xlsx", "pptx", "md", "html"])
def test_export_formats_match_the_engine(fmt) -> None:
    from beakr_cli._private_api.resources.outputs import EXPORT_FORMATS

    assert fmt in EXPORT_FORMATS
    assert len(EXPORT_FORMATS) == 6


@respx.mock
def test_markdown_export_is_written_as_the_text_beakr_sent(client, tmp_path) -> None:
    # Spec 3.11: md and html exports are text; the bytes on disk are what came back.
    body = "# KX2 report\n\nRecovery was 92% [1].\n"
    respx.get(f"{BASE}/v1/outputs/c1/export/md").mock(
        return_value=httpx.Response(
            200, content=body.encode(), headers={"content-type": "text/markdown; charset=utf-8"}
        )
    )
    path = client.outputs.export("c1", "md", tmp_path / "report.md")
    assert path.read_text() == body
