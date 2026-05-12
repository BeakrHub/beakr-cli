"""Validation helpers for wiki section metadata and inline citations."""

from __future__ import annotations

import re
from datetime import date

INLINE_SOURCE_TYPES = {"conversation", "agent_note", "user_note"}
VALID_STANCES = {"support", "contradicts", "qualifies"}
VALID_DATE_PRECISIONS = {"day", "month", "quarter", "year", "approx"}

CITATION_KEY_RE = re.compile(r"^[a-z_]+:[a-zA-Z0-9_.:/-]+$")
INLINE_CITATION_RE = re.compile(r"\{\{!?([a-z_]+:[a-zA-Z0-9_.:/-]+)\}\}")
ANY_INLINE_TOKEN_RE = re.compile(r"\{\{!?([^{}]+)\}\}")
SECTION_MARKER_RE = re.compile(r"<!--\s*sec:(\S+)\s*-->\s*")


class CitationValidationError(ValueError):
    """Raised when proposal section metadata cannot support inline citations."""


def extract_inline_citation_keys(text: str) -> list[str]:
    """Return valid inline citation keys from wiki markdown."""
    return [m.group(1) for m in INLINE_CITATION_RE.finditer(text or "")]


def _extract_malformed_inline_tokens(text: str) -> list[str]:
    malformed: list[str] = []
    for match in ANY_INLINE_TOKEN_RE.finditer(text or ""):
        raw_key = match.group(1)
        key = raw_key.strip()
        if raw_key != key or not CITATION_KEY_RE.fullmatch(raw_key):
            malformed.append(key)
    return malformed


def _section_text_by_id(content: str) -> dict[str, str]:
    matches = list(SECTION_MARKER_RE.finditer(content or ""))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        sec_id = match.group(1)
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content or "")
        sections[sec_id] = (content or "")[start:end]
    return sections


def _split_citation_key(key: str) -> tuple[str, str]:
    source_type, _, source_id = str(key or "").partition(":")
    return source_type, source_id


def _citation_key(citation: dict) -> str:
    meta = citation.get("meta") or {}
    key = citation.get("key") or meta.get("citation_key")
    if key:
        return str(key)
    source_type = str(citation.get("source_type") or "")
    source_id = str(citation.get("source_id") or "")
    if source_type and source_id:
        return f"{source_type}:{source_id}"
    return ""


def _parse_iso_date(value: object, field: str, section_id: str, errors: list[str]) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        errors.append(f"section {section_id}: {field} must be YYYY-MM-DD.")
        return None


def validate_proposal_sections(
    content: str | None,
    sections: list[dict],
    *,
    require_content: bool,
) -> None:
    """Validate proposal sections before sending them to the API.

    When ``require_content`` is true, every inline token in the markdown must
    have a matching structured citation in that same section, and every
    structured citation must appear inline in the section body.
    """
    errors: list[str] = []

    if not isinstance(sections, list):
        raise CitationValidationError("sections must be a JSON array.")

    content = content or ""
    section_texts = _section_text_by_id(content) if require_content else {}
    inline_keys_by_section = {
        sec_id: set(extract_inline_citation_keys(section_text))
        for sec_id, section_text in section_texts.items()
    }

    if require_content:
        malformed = _extract_malformed_inline_tokens(content)
        if malformed:
            examples = ", ".join(sorted(set(malformed))[:5])
            errors.append(
                "inline citation tokens must look like {{source_type:source_id}} "
                f"or {{{{!source_type:source_id}}}}; invalid: {examples}."
            )
        all_inline_keys = set(extract_inline_citation_keys(content))
        if all_inline_keys and not section_texts:
            errors.append("inline citations require <!-- sec:ID --> section markers in content.")
        if all_inline_keys and not sections:
            errors.append("content has inline citations, but sections is empty.")

    seen_section_ids: set[str] = set()
    citation_keys_by_section: dict[str, set[str]] = {}

    for sec_index, section in enumerate(sections):
        if not isinstance(section, dict):
            errors.append(f"sections[{sec_index}] must be an object.")
            continue

        section_id = str(section.get("id") or "")
        label = f"sections[{sec_index}]"
        if not section_id:
            errors.append(f"{label}: id is required.")
            continue
        label = f"section {section_id}"
        if section_id in seen_section_ids:
            errors.append(f"{label}: duplicate section id.")
        seen_section_ids.add(section_id)

        if require_content and section_id not in section_texts:
            errors.append(f"{label}: missing matching <!-- sec:{section_id} --> marker.")

        date_precision = section.get("date_precision")
        if date_precision and str(date_precision) not in VALID_DATE_PRECISIONS:
            allowed = ", ".join(sorted(VALID_DATE_PRECISIONS))
            errors.append(f"{label}: date_precision must be one of {allowed}.")

        event_start = _parse_iso_date(section.get("event_start"), "event_start", section_id, errors)
        event_end = _parse_iso_date(section.get("event_end"), "event_end", section_id, errors)
        if event_start and event_end and event_end < event_start:
            errors.append(f"{label}: event_end cannot be before event_start.")

        citations = section.get("citations") or []
        if not isinstance(citations, list):
            errors.append(f"{label}: citations must be an array.")
            continue

        citation_keys: set[str] = set()
        for cit_index, citation in enumerate(citations):
            cit_label = f"{label} citations[{cit_index}]"
            if not isinstance(citation, dict):
                errors.append(f"{cit_label}: citation must be an object.")
                continue

            meta = citation.get("meta") or {}
            if not isinstance(meta, dict):
                errors.append(f"{cit_label}: meta must be an object.")
                meta = {}

            key = _citation_key(citation)
            source_type = str(citation.get("source_type") or "")
            source_id = str(citation.get("source_id") or "")
            if key:
                if not CITATION_KEY_RE.fullmatch(key):
                    errors.append(
                        f"{cit_label}: key must match source_type:source_id using only "
                        "lowercase/underscore source types and token-safe IDs."
                    )
                key_type, key_id = _split_citation_key(key)
                source_type = source_type or key_type
                source_id = source_id or key_id
            else:
                errors.append(f"{cit_label}: provide key or source_type plus source_id.")

            stance = str(citation.get("stance") or "support")
            if stance not in VALID_STANCES:
                allowed = ", ".join(sorted(VALID_STANCES))
                errors.append(f"{cit_label}: stance must be one of {allowed}.")

            if source_type in INLINE_SOURCE_TYPES:
                source_title = str(citation.get("source_title") or "")
                excerpt = meta.get("excerpt") or meta.get("content") or meta.get("text")
                if not source_title:
                    errors.append(f"{cit_label}: inline {source_type} needs source_title.")
                if not excerpt:
                    errors.append(
                        f"{cit_label}: inline {source_type} needs meta.excerpt, "
                        "meta.content, or meta.text."
                    )
            elif source_type and not source_id:
                errors.append(f"{cit_label}: {source_type} citations require source_id.")

            if key:
                citation_keys.add(key)

        citation_keys_by_section[section_id] = citation_keys

    if require_content:
        for section_id, inline_keys in inline_keys_by_section.items():
            declared_keys = citation_keys_by_section.get(section_id, set())
            missing = inline_keys - declared_keys
            if missing:
                keys = ", ".join(sorted(missing))
                errors.append(
                    f"section {section_id}: inline citations missing from sections: {keys}."
                )

        for section_id, declared_keys in citation_keys_by_section.items():
            inline_keys = inline_keys_by_section.get(section_id, set())
            unused = declared_keys - inline_keys
            if unused:
                keys = ", ".join(sorted(unused))
                errors.append(f"section {section_id}: citations not used inline: {keys}.")

    if errors:
        raise CitationValidationError("\n".join(f"- {error}" for error in errors))
