"""Named list commands render the page envelope the public contract declares.

Root cause this guards (C064): several renderers read the list's retired key
(``threads``, ``revisions``, ``results``, ``events``) while the contract puts
every list's rows under ``data``, so a non-empty page printed "none" and the
command still exited 0. Each case here checks the contract's response schema
for its path really is a page with ``data``, then feeds that shape and requires
the row to be shown, so a renderer that drifts from the contract fails.
"""

from __future__ import annotations

import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.main import internal_app as app

API = "https://api.test"
PAGE = "11111111-1111-1111-1111-111111111111"

# (argv, contract path, row, text that must appear, extra GET mocks)
CASES = [
    (
        ["runs", "ls"],
        "/v1/me/threads",
        {"thread_id": "t1", "title": "Calibration review", "latest_run_id": "r42"},
        "r42",
        {},
    ),
    (
        ["outputs", "history", "c1"],
        "/v1/outputs/{output_id}/revisions",
        {"revision_no": 2, "summary": "Tightened intro"},
        "Tightened intro",
        {},
    ),
    (
        ["outputs", "ls"],
        "/v1/artifacts",
        {"id": "o1", "title": "Assay summary", "created_at": "2026-10-01"},
        "Assay summary",
        {},
    ),
    (
        ["files", "versions", "a1"],
        "/v1/artifacts/{artifact_id}/versions",
        {"id": "v1", "version_no": 4, "created_at": "2026-10-01"},
        "v1",
        {},
    ),
    (
        ["kb", "search", "assay"],
        "/v1/knowledge/wiki/search",
        {"id": PAGE, "title": "Binding assay", "snippet": "plates"},
        "Binding assay",
        {},
    ),
    (
        ["kb", "log", PAGE],
        "/v1/knowledge/wiki/pages/{page_id}/revisions",
        {"revision": 3, "summary": "Added controls", "compiled_by": "agent"},
        "Added controls",
        {f"/v1/knowledge/wiki/pages/{PAGE}": {"id": PAGE, "title": "Binding assay"}},
    ),
    (
        ["kb", "timeline"],
        "/v1/knowledge/wiki/timeline",
        {"date": "2026-10-01", "title": "Plate reader recalibrated", "page_title": "Lab"},
        "recalibrated",
        {},
    ),
]


def _concrete(path: str) -> str:
    return (
        path.replace("{output_id}", "c1").replace("{artifact_id}", "a1").replace("{page_id}", PAGE)
    )


@pytest.fixture(scope="module")
def contract() -> dict:
    from tests._engine import cli_reachable_contract

    # Fails instead of skipping under BEAKR_REQUIRE_ENGINE=1 (CI). The documented
    # contract plus the CLI's allowlisted operations: some of these lists (outputs,
    # versions, timeline) are unlisted routes.
    return cli_reachable_contract()


def _is_page(contract: dict, path: str) -> bool:
    schema = contract["paths"][path]["get"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ]
    name = schema.get("$ref", "").rsplit("/", 1)[-1]
    props = contract["components"]["schemas"].get(name, {}).get("properties", {})
    return {"data", "next_cursor", "has_more"} <= set(props)


@pytest.mark.parametrize(("argv", "path", "row", "visible", "extra"), CASES)
@respx.mock
def test_named_list_commands_render_contract_pages(
    monkeypatch, contract, argv, path, row, visible, extra
):
    assert _is_page(contract, path), f"{path} is no longer a page; update this case"
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)
    for extra_path, body in extra.items():
        respx.get(f"{API}{extra_path}").respond(200, json=body)
    respx.get(f"{API}{_concrete(path)}").respond(
        200, json={"data": [row], "next_cursor": None, "has_more": False}
    )
    result = CliRunner().invoke(app, argv)
    assert result.exit_code == 0, result.output
    assert visible in result.output
