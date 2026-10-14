"""What a citation is obliged to show an MCP reader.

The engine's source endpoints return the whole citation record -- excerpt,
fidelity, locator, url, artifact_id, availability, source_moved. Every tool here
used to receive all of it and print the type and the title, so an agent holding a
citation could not tell a checked quote from the compiler's paraphrase, could not
see where in a document a claim came from, and in ``kb_cat``'s case could not see
that the page had sources at all.

These pin the rendering contract rather than the wording: what must appear, what
must never appear, and what must survive a failure.
"""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from beakr_cli.mcp_server import (
    _excerpt_line,
    _source_lines,
    knowledge_base,
)


@pytest.fixture(autouse=True)
def base_url(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", "https://api.example.com")
    monkeypatch.setenv("BEAKR_API_TOKEN", "test-token")


# ---------------------------------------------------------------------------
# The verdict must travel with the excerpt
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fidelity", "expected"),
    [
        ("verbatim", ""),
        ("normalized", "(normalized)"),
        ("asserted", "(paraphrase)"),
        ("human_asserted", "(human asserted)"),
    ],
)
def test_excerpt_carries_its_verdict(fidelity: str, expected: str) -> None:
    line = _excerpt_line({"excerpt": "pH was 8.4", "fidelity": fidelity}, excerpt_chars=None)
    assert line is not None
    assert '"pH was 8.4"' in line
    if expected:
        assert expected in line
    else:
        assert "(" not in line, "a verbatim quote must not be qualified"


def test_missing_fidelity_is_called_unverified() -> None:
    """Absent is not the same as verified, and the default downstream is to trust it.

    The frontend gate treats an absent or unrecognised fidelity as trusted, so a
    quote that was never checked renders with quotation marks vouching for it
    unless the absence is stated. Silence here fails OPEN.
    """
    line = _excerpt_line({"excerpt": "pH was 8.4"}, excerpt_chars=None)
    assert line is not None
    assert "(unverified)" in line


def test_truncation_never_drops_the_verdict() -> None:
    line = _excerpt_line({"excerpt": "x" * 500, "fidelity": "asserted"}, excerpt_chars=50)
    assert line is not None
    assert "..." in line
    assert "(paraphrase)" in line


def test_no_excerpt_yields_no_line() -> None:
    assert _excerpt_line({}, excerpt_chars=None) is None
    assert _excerpt_line({"excerpt": "   "}, excerpt_chars=None) is None


def test_source_lines_show_verified_surrounding_context_separately() -> None:
    rendered = "\n".join(
        _source_lines(
            {
                "excerpt": "Launch moved to September.",
                "fidelity": "verbatim",
                "context_excerpt": (
                    "The steering group reviewed the dependency risk. "
                    "Launch moved to September. Owners accepted the revised plan."
                ),
            },
            indent="  ",
            excerpt_chars=None,
        )
    )
    assert '"Launch moved to September."' in rendered
    assert "context: The steering group reviewed" in rendered


# ---------------------------------------------------------------------------
# The access affordance, and the one this surface must never emit
# ---------------------------------------------------------------------------


def test_no_connector_handle_is_ever_emitted() -> None:
    """A handle is redeemed by a download tool, and this surface has none.

    The in-product path mints a per-caller handle because it runs inside the
    tool runtime with the caller's ctx. MCP has no tool that accepts one, so
    emitting it would export a connector uuid to an external client that could
    do nothing with it. The url is what an MCP reader can actually act on.
    """
    rendered = "\n".join(
        _source_lines(
            {
                "source_provider": "dropbox",
                "provider_file_id": "id:Zp-GhIn",
                "url": "https://dropbox.example/r.pdf",
            },
            indent="  ",
            excerpt_chars=None,
        )
    )
    assert "url: https://dropbox.example/r.pdf" in rendered
    assert "fetch" not in rendered
    assert "id:Zp-GhIn" not in rendered, "the provider file id is not actionable here"


def test_owned_artifacts_do_not_expose_internal_ids() -> None:
    rendered = "\n".join(
        _source_lines({"artifact_id": "9f1c-aaaa"}, indent="  ", excerpt_chars=None)
    )
    assert "9f1c-aaaa" not in rendered


def test_gone_sources_say_so() -> None:
    rendered = "\n".join(
        _source_lines(
            {"source_availability": "deleted_upstream", "unit_kind": "file"},
            indent="  ",
            excerpt_chars=None,
        )
    )
    assert "deleted_upstream" in rendered
    assert "unavailable: source is deleted_upstream" in rendered


def test_a_moved_source_is_flagged_distinctly_from_a_stale_anchor() -> None:
    """source_moved says the SOURCE has a newer reading, not that the quote broke."""
    rendered = "\n".join(_source_lines({"source_moved": True}, indent="  ", excerpt_chars=None))
    assert "source changed since cited" in rendered


def test_absent_facts_print_nothing_rather_than_blanks() -> None:
    assert _source_lines({}, indent="  ", excerpt_chars=None) == []


@pytest.mark.asyncio
@respx.mock
async def test_shared_sections_response_keeps_explicit_date_metadata() -> None:
    route = respx.post("https://api.example.com/v1/knowledge/wiki/command").mock(
        return_value=Response(
            200,
            json={
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Section date: event_start=2026-07-01; "
                            "event_end=2026-07-03; date_precision=approx"
                        ),
                    }
                ]
            },
        )
    )

    result = await knowledge_base(
        command="sections",
        arguments={"query": "incident"},
        scope="Research",
    )

    assert "event_start=2026-07-01" in result["content"]
    assert "event_end=2026-07-03" in result["content"]
    assert "date_precision=approx" in result["content"]
    assert route.called


@pytest.mark.asyncio
@respx.mock
async def test_wiki_read_returns_public_source_location_and_exact_evidence() -> None:
    route = respx.post("https://api.example.com/v1/knowledge/wiki/command").mock(
        return_value=Response(
            200,
            json={
                "content": [{"type": "text", "text": "Launch plan"}],
                "sources": [
                    {
                        "source_ref": "beakr-source:v1:110f8b8c-a8fe-4c91-92e8-55ad0f481234",
                        "citation_key": "dropbox:launch-plan",
                        "name": "Launch Plan",
                        "filename": "launch-plan.docx",
                        "provider": "dropbox",
                        "location": {
                            "url": "https://dropbox.example/launch-plan",
                            "display_path": "/Plans/launch-plan.docx",
                        },
                        "evidence": {
                            "excerpt": "Launch is approved for September 4.",
                            "locator": "page 4",
                            "fidelity": "verbatim",
                        },
                        "retrieval": {
                            "tool": "dropbox_download",
                            "arguments": {
                                "handle": "beakr://external/caller/dropbox/launch-plan"
                            },
                        },
                        "unit_id": "must-not-leak",
                        "connector_id": "must-not-leak",
                    }
                ],
            },
        )
    )

    result = await knowledge_base(command="cat", arguments={"page": "Launch"})

    assert route.called
    assert result["content"] == "Launch plan"
    assert result["sources"] == [
        {
            "source_ref": "beakr-source:v1:110f8b8c-a8fe-4c91-92e8-55ad0f481234",
            "citation_key": "dropbox:launch-plan",
            "name": "Launch Plan",
            "filename": "launch-plan.docx",
            "provider": "dropbox",
            "location": {
                "url": "https://dropbox.example/launch-plan",
                "display_path": "/Plans/launch-plan.docx",
            },
            "evidence": {
                "excerpt": "Launch is approved for September 4.",
                "locator": "page 4",
                "fidelity": "verbatim",
            },
            "retrieval": {
                "tool": "dropbox_download",
                "arguments": {"handle": "beakr://external/caller/dropbox/launch-plan"},
            },
        }
    ]
    assert "unit_id" not in str(result)
    assert "connector_id" not in str(result)
