"""No request URL reaches stderr by default: a presigned S3 URL's signature is a credential.

``beakr mcp`` builds an mcp ``MCPServer``, whose ``configure_logging`` puts a
RichHandler at INFO on the root logger. httpx logs every request at INFO
("HTTP Request: PUT <url> ..."), so an upload's presigned PUT URL, with its
X-Amz-Signature, went to stderr: the MCP client's log. Each test runs in a
fresh interpreter, as ``beakr`` does, so the logging the rest of the suite set
up cannot hide or fake the leak.
"""

from __future__ import annotations

import os
import subprocess
import sys

SIGNED = (
    "https://beakr-uploads.s3.amazonaws.com/o/part-1?X-Amz-Algorithm=AWS4-HMAC-SHA256"
    "&X-Amz-Credential=AKIAEXAMPLE%2F20261126%2Fus-east-1%2Fs3%2Faws4_request"
    "&X-Amz-Signature=5f2b0c9e8d7a6b5c4d3e2f1a0b9c8d7e6f5a4b3c2d1e0f9a8b7c6d5e4f3a2b1c"
)

_REQUEST = f"""
import httpx
client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
client.put({SIGNED!r}, content=b"part")
client.get("https://api.test/v1/artifacts/a1")
"""


def _squashed(text: str) -> str:
    """stderr without whitespace: Rich wraps a long URL across lines, which would hide it
    from a plain substring check."""
    return "".join(text.split())


def _run(code: str, **env: str) -> subprocess.CompletedProcess[str]:
    environ = {k: v for k, v in os.environ.items() if not k.startswith("BEAKR_LOG")}
    environ.update(env, BEAKR_API_URL="https://api.test", BEAKR_API_KEY="bk_live_test")
    return subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=environ, timeout=120
    )


def test_the_stdio_server_logs_no_request_url_by_default():
    result = _run("import beakr_cli.mcp_server\n" + _REQUEST)
    assert result.returncode == 0, result.stderr
    said = _squashed(result.stderr)
    assert "X-Amz-Signature" not in said and "5f2b0c9e8d7a6b5c" not in said
    assert "s3.amazonaws.com" not in said and "api.test" not in said
    assert "HTTPRequest" not in said


def test_the_server_start_keeps_them_quiet_too():
    """``run_server`` quiets them again, after anything that configured logging since."""
    code = (
        "import logging, beakr_cli.mcp_server as m\n"
        "logging.getLogger('httpx').setLevel(logging.INFO)\n"
        "m.mcp.run = lambda **kwargs: None\n"
        "m.run_server()\n" + _REQUEST
    )
    result = _run(code)
    assert result.returncode == 0, result.stderr
    said = _squashed(result.stderr)
    assert "X-Amz-Signature" not in said and "HTTPRequest" not in said


def test_asking_for_request_logs_shows_them_without_the_signature():
    result = _run("import beakr_cli.mcp_server\n" + _REQUEST, BEAKR_LOG_HTTP="1")
    assert result.returncode == 0, result.stderr
    said = _squashed(result.stderr)
    assert "HTTPRequest" in said and "api.test/v1/artifacts/a1" in said
    assert "s3.amazonaws.com/o/part-1" in said and "X-Amz-Signature=<redacted>" in said
    assert "5f2b0c9e8d7a6b5c" not in said and "AKIAEXAMPLE" not in said


def test_a_cli_command_logs_no_request_url():
    """Outside ``beakr mcp`` nothing configures logging: httpx's INFO lines never print."""
    code = (
        "import sys, respx\n"
        "from beakr_cli.main import app\n"
        "with respx.mock:\n"
        "    respx.get('https://api.test/v1/artifacts/a1').respond(\n"
        "        200, json={'id': 'a1', 'title': 'Assay notes'})\n"
        "    sys.argv = ['beakr', 'files', 'show', 'a1']\n"
        "    try:\n"
        "        app()\n"
        "    except SystemExit as exit:\n"
        "        assert not exit.code, exit.code\n" + _REQUEST
    )
    result = _run(code)
    assert result.returncode == 0, result.stderr
    assert "a1\tAssay notes" in result.stdout
    said = _squashed(result.stderr)
    assert "api.test" not in said and "HTTPRequest" not in said
    assert "X-Amz-Signature" not in said
