"""``beakr projects refusals``: a project's model refusals (engine #1765), summary then files."""

from __future__ import annotations

import respx
from typer.testing import CliRunner

from beakr_cli.main import app

API = "https://api.test"
PROJECT = "4dd25154-0d80-44ab-b4c2-167319bfacb4"
FILE = "3f6a1c2e-7b4d-4e9a-9c1f-2d8e5b7a6c40"


def _env(monkeypatch) -> None:
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


@respx.mock
def test_refusals_prints_the_summary_then_each_file(isolated_home, monkeypatch) -> None:
    _env(monkeypatch)
    base = f"{API}/v1/projects/{PROJECT}/model-refusals"
    summary = respx.get(f"{base}/summary").respond(
        json={
            "window": "30d",
            "refusals": 3,
            "fallbacks": 2,
            "files_affected": 1,
            "outcomes": {"recovered": 2, "failed": 1, "skipped": 0},
        }
    )
    files = respx.get(f"{base}/files").respond(
        json={
            "data": [
                {
                    "artifact_id": FILE,
                    "title": "variants.vcf",
                    "refusals": 3,
                    "fallbacks": 2,
                    "failed": 1,
                    "recovered": 2,
                    "skipped": 0,
                }
            ],
            "has_more": False,
        }
    )

    result = CliRunner().invoke(app, ["projects", "refusals", PROJECT, "--window", "30d"])

    assert result.exit_code == 0, result.output
    assert summary.calls.last.request.url.params["window"] == "30d"
    assert files.calls.last.request.url.params["window"] == "30d"
    lines = result.stdout.splitlines()
    assert lines[0] == "30d: 3 refusals, 2 fallbacks, 1 files; recovered=2 failed=1 skipped=0"
    assert lines[1].split("\t")[:2] == [FILE, "variants.vcf"]
    assert "refusals=3" in lines[1]


def test_refusals_refuses_an_unknown_window(isolated_home, monkeypatch) -> None:
    _env(monkeypatch)
    result = CliRunner().invoke(app, ["projects", "refusals", PROJECT, "--window", "1y"])
    assert result.exit_code == 2
