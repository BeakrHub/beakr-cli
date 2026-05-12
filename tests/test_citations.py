from __future__ import annotations

import pytest

from beakr_cli.citations import CitationValidationError, validate_proposal_sections


def test_validates_inline_conversation_source() -> None:
    content = """<!-- sec:decision -->
## Decision

The team kept the May launch date {{agent_note:launch-risk}}.
"""
    sections = [
        {
            "id": "decision",
            "event_start": "2026-05-11",
            "date_precision": "day",
            "citations": [
                {
                    "key": "agent_note:launch-risk",
                    "source_type": "agent_note",
                    "source_title": "Agent synthesis from current conversation",
                    "stance": "support",
                    "meta": {
                        "excerpt": "The team kept the launch date but added a rollback gate."
                    },
                }
            ],
        }
    ]

    validate_proposal_sections(content, sections, require_content=True)


def test_rejects_inline_token_without_section_citation() -> None:
    content = """<!-- sec:financials -->
## Financials

Revenue was $12.4M {{rag:abc123}}.
"""

    with pytest.raises(CitationValidationError) as exc:
        validate_proposal_sections(content, [{"id": "financials"}], require_content=True)

    assert "inline citations missing from sections: rag:abc123" in str(exc.value)


def test_rejects_section_citation_not_used_inline() -> None:
    content = """<!-- sec:financials -->
## Financials

Revenue was $12.4M.
"""
    sections = [{"id": "financials", "citations": [{"key": "rag:abc123"}]}]

    with pytest.raises(CitationValidationError) as exc:
        validate_proposal_sections(content, sections, require_content=True)

    assert "citations not used inline: rag:abc123" in str(exc.value)


def test_rejects_malformed_inline_token() -> None:
    content = """<!-- sec:financials -->
## Financials

Revenue was $12.4M {{rag:abc 123}}.
"""

    with pytest.raises(CitationValidationError) as exc:
        validate_proposal_sections(content, [], require_content=True)

    assert "inline citation tokens must look like" in str(exc.value)


def test_rejects_inline_token_with_spaces() -> None:
    content = """<!-- sec:financials -->
## Financials

Revenue was $12.4M {{ rag:abc123 }}.
"""

    with pytest.raises(CitationValidationError) as exc:
        validate_proposal_sections(content, [], require_content=True)

    assert "inline citation tokens must look like" in str(exc.value)


def test_rejects_inline_source_without_excerpt() -> None:
    content = """<!-- sec:decision -->
## Decision

The team kept the May launch date {{agent_note:launch-risk}}.
"""
    sections = [
        {
            "id": "decision",
            "citations": [
                {
                    "key": "agent_note:launch-risk",
                    "source_type": "agent_note",
                    "source_title": "Agent synthesis from current conversation",
                }
            ],
        }
    ]

    with pytest.raises(CitationValidationError) as exc:
        validate_proposal_sections(content, sections, require_content=True)

    assert "inline agent_note needs meta.excerpt" in str(exc.value)
