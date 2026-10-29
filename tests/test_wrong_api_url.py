"""A wrong API URL is named, never printed as the page it answered.

Root cause: the SDK took a body that is not JSON (the HTML page a web app, a
proxy or another server answers) as the error message, and the CLI printed it:
pointed at a wrong URL, ``beakr`` printed the page's raw HTML as the error.
``beakr research`` also caught every error itself, so it printed the page and
exited 1 whatever the refusal. ``beakr doctor`` called any HTTP answer from
the API URL "ok", an HTML 404 from the wrong host included.
"""

from __future__ import annotations

import json

import httpx
import respx
from typer.testing import CliRunner

from beakr_cli.main import app
from beakr_cli.mcp_errors import error_fields

API = "https://wrong.test"
runner = CliRunner()

_PAGE = """<!DOCTYPE HTML>
<html lang="en">
    <head><title>Error response</title></head>
    <body><h1>Error response</h1><p>Error code: 404</p></body>
</html>
"""


def _html(status: int, page: str = _PAGE) -> httpx.Response:
    return httpx.Response(status, text=page, headers={"content-type": "text/html"})


def _env(monkeypatch) -> None:
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


@respx.mock
def test_an_html_404_names_the_address_and_the_api_url_settings(monkeypatch):
    _env(monkeypatch)
    respx.get(f"{API}/v1/projects").mock(return_value=_html(404))

    result = runner.invoke(app, ["projects", "ls"], env={"COLUMNS": "400"})

    assert result.exit_code == 4
    assert "<html" not in result.output and "DOCTYPE" not in result.output
    assert (
        f'{API}/v1/projects answered HTTP 404 with an HTML page ("Error response"), '
        "not the Beakr API." in result.output
    )
    assert "--api-url, BEAKR_BASE_URL" in result.output
    assert "does not provide" not in result.output


@respx.mock
def test_the_json_error_carries_the_short_message_and_the_fix(monkeypatch):
    _env(monkeypatch)
    respx.get(f"{API}/v1/projects").mock(return_value=_html(404))

    result = runner.invoke(app, ["projects", "ls", "--json"])

    error = json.loads(result.stdout)["error"]
    assert "<html" not in json.dumps(error)
    assert error["message"].startswith(f"{API}/v1/projects answered HTTP 404 with an HTML page")
    assert error["fix"].startswith("Check the API URL: --api-url")
    assert error["status"] == 404 and error["exit_code"] == 4


@respx.mock
def test_research_reports_a_gateway_page_through_the_root_handler(monkeypatch):
    _env(monkeypatch)
    respx.post(f"{API}/v1/knowledge/research").mock(
        return_value=_html(502, "<html><title>502 Bad Gateway</title></html>")
    )

    result = runner.invoke(app, ["research", "What changed?"], env={"COLUMNS": "400"})

    assert result.exit_code == 1
    assert "<html" not in result.output
    assert '("502 Bad Gateway")' in result.output
    assert "retry shortly" in result.output


@respx.mock
def test_research_exits_with_the_refusals_own_code(monkeypatch):
    _env(monkeypatch)
    respx.post(f"{API}/v1/knowledge/research").respond(
        401, json={"error": {"code": "unauthorized", "message": "Invalid API key"}}
    )

    result = runner.invoke(app, ["research", "What changed?"])

    assert result.exit_code == 3, result.output


@respx.mock
def test_doctor_fails_the_api_check_on_an_html_page(monkeypatch):
    _env(monkeypatch)
    respx.get(f"{API}/v1/public/openapi.json").mock(return_value=_html(404))
    respx.get(f"{API}/v1/me").mock(return_value=_html(404))

    result = runner.invoke(app, ["doctor", "--json"])

    checks = {c["name"]: c for c in json.loads(result.stdout)["checks"]}
    assert checks["api"]["status"] == "fail"
    assert "with an HTML page" in checks["api"]["detail"]
    assert "<html" not in checks["auth"]["detail"]


def test_an_mcp_tool_error_names_the_page_too():
    request = httpx.Request("POST", f"{API}/v1/knowledge/wiki/command")
    response = httpx.Response(
        404, text=_PAGE, headers={"content-type": "text/html"}, request=request
    )

    fields = error_fields(response)

    assert fields["message"].startswith(f"{API}/v1/knowledge/wiki/command answered HTTP 404")
    assert "<html" not in fields["message"]
    assert fields["fix"].startswith("Check the API URL")


def test_a_json_404_is_still_a_missing_record_or_route():
    request = httpx.Request("GET", f"{API}/v1/projects/p1")
    response = httpx.Response(404, json={"detail": "Not Found"}, request=request)

    assert error_fields(response)["error_code"] == "not_provided"
