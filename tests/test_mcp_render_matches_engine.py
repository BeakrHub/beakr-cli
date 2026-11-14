"""``beakr_cli.mcp_render`` carries the hosted renderer's helpers, byte for byte.

Both MCP servers send a tool's result as the same text, so the stdio server
carries a copy of the engine's ``beakr/api/mcp_remote/render.py`` from
``organization_line`` to the end of the file: the Sources list (one entry per
source identity, same-named sources told apart), the brief line, the approval
state line and the organization line. The two repositories share no package;
this fails the moment the copy drifts.

Root cause this guards: the copy was kept by hand, function by function, and
had already fallen behind (the hosted list labelled a compiler-written excerpt
``(paraphrase)`` and the stdio list did not) before the identity-keyed Sources
list changed it again.
"""

from __future__ import annotations

from pathlib import Path

from tests._engine import engine_checkout

_MIRROR = Path(__file__).resolve().parents[1] / "src" / "beakr_cli" / "mcp_render.py"
_START = "def organization_line("


def _shared(text: str) -> str:
    return text[text.index(_START) :].rstrip()


def test_the_copy_matches_the_engine():
    engine = engine_checkout("beakr/api/mcp_remote/render.py").read_text()
    assert _shared(_MIRROR.read_text()) == _shared(engine), (
        "beakr_cli/mcp_render.py differs from the engine's render.py from "
        "organization_line on: copy it again"
    )


def test_the_shared_constants_match_the_engine():
    # Above the copied region the stdio module declares its own schema type, so
    # the values the copied functions read are compared one by one.
    from beakr_cli import mcp_render
    from tests._engine import hosted_render

    hosted = hosted_render()
    for name in (
        "EXCERPT_CHARS",
        "LOCATION_SEPARATOR",
        "SOURCES_CHARS",
        "PLACES_PER_DOCUMENT",
        "_SOURCE_LISTING_TOOLS",
        "UPLOAD_PROVIDER",
        "SOURCE_MODES",
        "DEFAULT_SOURCE_MODE",
        "SOURCE_MODE_KEY",
        "CITE_HINT",
    ):
        assert getattr(mcp_render, name) == getattr(hosted, name), name


def _granola(note_id: str, date: str) -> dict:
    return {
        "citation_key": f"external_item:{note_id}-u-m1",
        "name": "Granola: Albert",
        "provider": "granola",
        "source_ref": f"beakr-source:v1:{note_id}",
        "evidence": {"excerpt": "Albert agreed.", "source_recorded_at": f"{date}T10:00:00Z"},
    }


def test_render_is_the_hosted_render_for_what_changed_above_the_copy():
    # ``render`` sits above the copied region. Two call notes with one title are
    # two sources (they were one line, keyed by the name they share), and with
    # ``sources='none'`` the step that says to cite from the list goes with it.
    from beakr_cli import mcp_render
    from tests._engine import hosted_render

    hosted = hosted_render()
    notes = [
        _granola("11111111-1111-4111-8111-111111111111", "2026-11-03"),
        _granola("22222222-2222-4222-8222-222222222222", "2026-11-10"),
    ]
    for mode in ("brief", "full", "none"):
        body = {
            "content": "Albert agreed twice.",
            "sources": notes,
            "source_listing": mode,
            "next": [mcp_render.CITE_HINT, "Read on with cat."],
        }
        text = mcp_render.render("knowledge_base", body)
        assert text == hosted.render("knowledge_base", body), mode
        if mode == "none":
            assert text == "Albert agreed twice.\n\nnext: Read on with cat."
        else:
            assert "Granola: Albert · 2026-11-03" in text
            assert "Granola: Albert · 2026-11-10" in text
            assert f"next: {mcp_render.CITE_HINT}" in text
