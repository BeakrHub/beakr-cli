"""``beakr_cli.mcp_knowledge_status`` is the engine's notice, byte for byte.

Both MCP servers append the same Knowledge notice to ``list_projects`` (which
projects' Knowledge is still building or has failed files), so the stdio server
carries a copy of the engine's ``beakr/api/mcp_remote/knowledge_status.py``. The
two repositories share no package; this fails the moment the copy drifts.
"""

from __future__ import annotations

from pathlib import Path

from tests._engine import engine_checkout

_MIRROR = Path(__file__).resolve().parents[1] / "src" / "beakr_cli" / "mcp_knowledge_status.py"
_START = "MAX_NOTICE_PROJECTS = "


def _shared(text: str) -> str:
    return text[text.index(_START) :].rstrip()


def test_the_copy_matches_the_engine():
    engine = engine_checkout("beakr/api/mcp_remote/knowledge_status.py").read_text()
    assert _shared(_MIRROR.read_text()) == _shared(engine), (
        "beakr_cli/mcp_knowledge_status.py differs from the engine's knowledge_status.py: "
        "copy it again"
    )
