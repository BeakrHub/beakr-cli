"""What a citation is obliged to show an MCP reader.

The engine's source endpoints return the whole citation record -- excerpt,
whose words it is, locator, url, artifact_id, availability, source_moved. Every
tool here used to receive all of it and print the type and the title, so an agent
holding a citation could not tell the source's words from Beakr's own, could not
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
    # BEAKR_API_KEY, not the BEAKR_API_TOKEN this used to set: nothing reads
    # that name, so these tests silently authenticated with the developer's
    # real saved key and failed on any machine without one.
    monkeypatch.setenv("BEAKR_API_KEY", "test-token")


# ---------------------------------------------------------------------------
# An excerpt is shown by whose words it is, with no check state
#
# These lines used to carry a verdict -- ``(normalized)``, ``(paraphrase)``,
# ``(human asserted)``, ``(unverified)`` -- so a result talked about checking
# instead of about the source, and a person's attribution still sat in quotation
# marks. The engine's rule (its ``services/evidence/display.py``) now decides:
# only the source's own words are quoted and other text says who wrote it (a
# typed quote that was not found in its source reads as Beakr's wording). These are the
# columns an engine from before ``evidence.excerpt_kind`` sends.
# ---------------------------------------------------------------------------

_CHECK_WORDS = ("(unverified)", "(paraphrase)", "(human asserted)", "(normalized)")


@pytest.mark.parametrize(
    ("columns", "expected"),
    [
        ({"fidelity": "verbatim"}, '"pH was 8.4"'),
        ({"fidelity": "normalized"}, '"pH was 8.4"'),
        ({"quote_origin": "located", "fidelity": "asserted"}, '"pH was 8.4"'),
        ({"excerpt_kind": "quote"}, '"pH was 8.4"'),
        # A passage a search returned carries no column at all: the source's text.
        ({}, '"pH was 8.4"'),
        ({"fidelity": "human_asserted"}, "Added by a person: pH was 8.4"),
        ({"quote_origin": "human"}, "Added by a person: pH was 8.4"),
        (
            {"type": "external_record", "fidelity": "human_asserted"},
            "Added by a person: pH was 8.4",
        ),
        ({"excerpt_kind": "person"}, "Added by a person: pH was 8.4"),
        ({"quote_origin": "authored", "fidelity": "asserted"}, "Described by Beakr: pH was 8.4"),
        ({"evidence_kind": "description"}, "Described by Beakr: pH was 8.4"),
        ({"excerpt_kind": "description"}, "Described by Beakr: pH was 8.4"),
        # A typed quote the source search did not find: shown, never quoted.
        ({"fidelity": "asserted", "evidence_kind": "exact"}, "Described by Beakr: pH was 8.4"),
    ],
)
def test_an_excerpt_is_shown_by_its_origin(columns: dict, expected: str | None) -> None:
    line = _excerpt_line({"excerpt": "pH was 8.4", **columns}, excerpt_chars=None)

    assert line == expected
    for word in _CHECK_WORDS:
        assert word not in (line or "")


def test_truncation_never_drops_the_origin_label() -> None:
    line = _excerpt_line({"excerpt": "x" * 500, "quote_origin": "authored"}, excerpt_chars=50)
    assert line is not None
    assert "..." in line
    assert line.startswith("Described by Beakr: ")


def test_the_shared_record_keeps_the_origin_and_drops_the_check_state() -> None:
    from beakr_cli.mcp_server import _public_source

    new_engine = _public_source(
        {
            "source_ref": "beakr-source:v1:abc",
            "name": "reads.fastq",
            "evidence": {
                "excerpt": "a computed mean",
                "excerpt_kind": "description",
                "evidence_kind": "description",
            },
            "excerpts": [
                {"text": "mean,12.4"},
                {"text": "a computed mean", "excerpt_kind": "description", "authored": True},
            ],
        }
    )
    assert new_engine["evidence"] == {"excerpt": "a computed mean", "excerpt_kind": "description"}
    assert new_engine["excerpts"] == [
        {"text": "mean,12.4"},
        {"text": "a computed mean", "excerpt_kind": "description"},
    ]

    # An engine from before ``excerpt_kind``: the same rule, worked out here.
    older = _public_source(
        {
            "source_ref": "beakr-source:v1:abc",
            "name": "memo.pdf",
            "evidence": {"excerpt": "retyped", "fidelity": "asserted", "anchor_status": "stale"},
        }
    )
    assert older["evidence"] == {"excerpt": "retyped", "excerpt_kind": "description"}
    quoted = _public_source(
        {
            "source_ref": "beakr-source:v1:abc",
            "name": "memo.pdf",
            "evidence": {"excerpt": "the words", "fidelity": "verbatim", "locator": "p. 7"},
        }
    )
    assert quoted["evidence"] == {
        "excerpt": "the words",
        "locator": "p. 7",
        "excerpt_kind": "quote",
    }


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
                            "arguments": {"handle": "beakr://external/caller/dropbox/launch-plan"},
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
                # Whose words it is; never the check state an older engine sends.
                "excerpt_kind": "quote",
            },
            "retrieval": {
                "tool": "dropbox_download",
                "arguments": {"handle": "beakr://external/caller/dropbox/launch-plan"},
            },
        }
    ]
    assert "unit_id" not in str(result)
    assert "connector_id" not in str(result)
