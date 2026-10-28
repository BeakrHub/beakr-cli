"""``beakr_cli.mcp_integration_results`` is the engine's text, byte for byte.

Both MCP servers answer the ``integrations`` tool with the same text (its tool
list, one tool's description, a call's result and Sources, and its refusals),
so the stdio server carries a copy of the engine's
``beakr/api/mcp_remote/integration_results.py``. The two repositories share no
package; this fails the moment the copy drifts.
"""

from __future__ import annotations

from pathlib import Path

from tests._engine import engine_checkout

_MIRROR = Path(__file__).resolve().parents[1] / "src" / "beakr_cli" / "mcp_integration_results.py"
_START = "COMMANDS = "


def _shared(text: str) -> str:
    return text[text.index(_START) :].rstrip()


def test_the_copy_matches_the_engine():
    engine = engine_checkout("beakr/api/mcp_remote/integration_results.py").read_text()
    assert _shared(_MIRROR.read_text()) == _shared(engine), (
        "beakr_cli/mcp_integration_results.py differs from the engine's "
        "integration_results.py: copy it again"
    )
