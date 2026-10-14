"""The CLI is built on the SDK: no CLI module talks HTTP to the Beakr API itself.

SPEC-457 ("rebuild beakr-cli on the Python SDK"). Before this change, kb,
workspace, auth, install, research and the stdio MCP server sent requests
through hand-built httpx clients in ``beakr_cli/client.py``, so they missed the
SDK's credential refresh, retries, typed errors and attribution headers, and an
API change had to be made in two places. This test fails if a CLI module
imports httpx (or brings the old helper back), except where the module is not
calling the Beakr API, each with its reason.
"""

from __future__ import annotations

import ast
from pathlib import Path

CLI_ROOT = Path(__file__).resolve().parents[1] / "src" / "beakr_cli"

#: CLI modules allowed to import httpx, and why none of them is a Beakr API call
#: the SDK should make.
HTTPX_ALLOWED = {
    # Checks PyPI for a newer release: not the Beakr API.
    "updates.py": "PyPI release check",
    # Fetches the public, unauthenticated OpenAPI document to report whether the
    # server is reachable, before any credential exists.
    "commands/doctor.py": "unauthenticated reachability probe",
    # Formats an httpx response the SDK raised into the tool error text, and
    # keeps ApiCallError an httpx.HTTPStatusError for callers reading .response.
    "mcp_errors.py": "error wording over the SDK's response",
}


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_no_cli_module_imports_httpx_without_a_reason() -> None:
    offenders = sorted(
        str(path.relative_to(CLI_ROOT))
        for path in CLI_ROOT.rglob("*.py")
        if "httpx" in _imports(path) and str(path.relative_to(CLI_ROOT)) not in HTTPX_ALLOWED
    )
    assert offenders == [], f"Call the API through beakr_sdk instead: {offenders}"


def test_the_legacy_http_helper_is_gone() -> None:
    assert not (CLI_ROOT / "client.py").exists()
    offenders = sorted(
        str(path.relative_to(CLI_ROOT))
        for path in CLI_ROOT.rglob("*.py")
        if "beakr_cli.client" in _imports(path)
    )
    assert offenders == []


def test_every_allowance_is_still_needed() -> None:
    stale = sorted(name for name in HTTPX_ALLOWED if "httpx" not in _imports(CLI_ROOT / name))
    assert stale == [], f"No longer imports httpx; drop the allowance: {stale}"
