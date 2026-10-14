"""beakr-bench: case files validate, and the deterministic checks mean what they say."""

from __future__ import annotations

import json

import pytest
from beakr_bench import SUITES
from beakr_bench.cases import CaseFileError, load_suite
from beakr_bench.extract import answer_sources, answer_text, compound_status, proposal_ids
from beakr_bench.scoring import (
    acknowledges_partial,
    claims_completeness,
    has_citation,
    score_answer,
)


@pytest.mark.parametrize("suite", SUITES)
def test_every_suite_loads_and_every_case_asks_something(suite):
    description, cases = load_suite(suite)
    assert description
    assert cases
    for case in cases:
        assert case.asks, case.case_id


def test_compounding_asks_are_on_an_episode_clock():
    _, cases = load_suite("compounding")
    for case in cases:
        episodes = [ask["episode"] for ask in case.asks]
        assert all(isinstance(e, int) for e in episodes), case.case_id
        assert episodes == sorted(episodes), case.case_id
        assert len(set(episodes)) >= 2, case.case_id


def test_a_malformed_case_file_is_refused(tmp_path):
    bad = {
        "suite": "corrections",
        "cases": [{"id": "x", "steps": [{"op": "ask", "label": "a", "question": "q"}]}],
    }
    (tmp_path / "corrections.json").write_text(json.dumps(bad))
    with pytest.raises(CaseFileError, match="without checks"):
        load_suite("corrections", data_dir=tmp_path)


def test_unknown_check_names_are_refused(tmp_path):
    bad = {
        "suite": "corrections",
        "cases": [
            {
                "id": "x",
                "steps": [
                    {"op": "ask", "label": "a", "question": "q", "checks": {"must_inclde": ["x"]}}
                ],
            }
        ],
    }
    (tmp_path / "corrections.json").write_text(json.dumps(bad))
    with pytest.raises(CaseFileError, match="unknown checks"):
        load_suite("corrections", data_dir=tmp_path)


def test_must_include_needs_one_alternative_from_every_group():
    checks = {"must_include": [["52 nM", "52nM"], ["recalibrated"]]}
    assert score_answer("Kd is 52nM on a recalibrated chip.", [], checks).passed
    result = score_answer("Kd is 52 nM.", [], checks)
    assert not result.passed and result.details["missing"] == ["recalibrated"]


def test_stale_values_fail_must_not_include():
    result = score_answer("Kd is 38 nM.", [], {"must_not_include": ["38 nM"]})
    assert not result.passed and result.details["forbidden_present"] == ["38 nM"]


@pytest.mark.parametrize(
    "answer",
    [
        "This is the complete list of sites.",
        "These are all the sites that enrolled.",
        "There were exactly three sites.",
        "No other site enrolled patients.",
    ],
)
def test_completeness_claims_are_caught(answer):
    assert claims_completeness(answer)
    assert not score_answer(answer, [], {"forbid_completeness_claim": True}).passed


@pytest.mark.parametrize(
    "answer",
    [
        "Based on the records available, three sites enrolled; others may exist.",
        "The record may be incomplete.",
        "I could only find three sites.",
        "This list is not exhaustive.",
    ],
)
def test_partial_acknowledgements_are_recognised(answer):
    assert acknowledges_partial(answer.lower())
    assert not claims_completeness(answer.lower())


def test_an_honest_partial_answer_passes_false_completeness_checks():
    checks = {
        "must_include": [["Lindqvist"], ["Okafor"]],
        "must_not_include": ["Saltmarsh"],
        "forbid_completeness_claim": True,
        "require_partial_acknowledgement": True,
    }
    answer = (
        "Lindqvist Clinic and Okafor Medical Center enrolled patients; "
        "the record may be incomplete."
    )
    assert score_answer(answer, [], checks).passed


def test_citations_come_from_sources_or_inline_markers():
    assert has_citation("plain", [{"title": "x"}])
    assert has_citation("Kd is 52 nM {{conversation:abc}}", [])
    assert has_citation("Kd is 52 nM [1]", [])
    assert not has_citation("Kd is 52 nM", [])


def test_an_ask_with_no_checks_never_passes():
    assert not score_answer("anything", [], {}).passed


def test_answers_and_sources_are_read_from_every_documented_shape():
    assert answer_text({"answer": "A", "sources": [1]}) == "A"
    assert answer_text({"content": [{"type": "text", "text": "B"}]}) == "B"
    assert answer_text({"format": "markdown", "content": "C", "blocks": []}) == "C"
    assert answer_text({"blocks": [{"name": "x", "content": "D"}]}) == "D"
    assert answer_sources({"answer": "A", "citations": [{"k": 1}]}) == [{"k": 1}]
    assert answer_sources({"answer": "A"}) == []


def test_proposal_ids_are_found_in_fields_and_text():
    response = {
        "proposal_id": "11111111-aaaa",
        "content": [{"type": "text", "text": 'staged {"proposal_id": "22222222-bbbb"}'}],
    }
    assert proposal_ids(response) == ["11111111-aaaa", "22222222-bbbb"]


def test_compound_status_is_read_from_the_conversation():
    assert compound_status({"compound_status": "completed"}) == "completed"
    assert compound_status({"conversation": {"compound_status": "partial"}}) == "partial"
    assert compound_status({}) is None


# The page types a new project's default ontology accepts. The write door
# refuses an alias ("process" for protocol) with invalid_page_type, so a case
# seeded with one errors on a real engine while the in-memory stand-in, which
# does not check types, passes it.
DEFAULT_PAGE_TYPES = {
    "compound",
    "dataset",
    "decision",
    "disease",
    "experiment",
    "index",
    "initiative",
    "meeting",
    "organization",
    "overview",
    "person",
    "protocol",
    "topic",
}


def test_every_seeded_page_uses_a_default_ontology_type():
    import json
    from pathlib import Path

    data = Path(__file__).resolve().parents[2] / "benchmarks" / "beakr_bench" / "data"
    used = set()

    def walk(node):
        if isinstance(node, dict):
            if "page_type" in node:
                used.add(node["page_type"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for path in data.glob("*.json"):
        walk(json.loads(path.read_text()))
    assert used <= DEFAULT_PAGE_TYPES, used - DEFAULT_PAGE_TYPES


def test_every_imported_session_clears_the_engines_capture_gate():
    # Beakr compounds an imported session only when its material is at least
    # 500 characters with two pieces of evidence (the capture gate in the
    # engine's wiki capture agent). The first handoff sessions were 230-355
    # characters, so a live run skipped every one and the suite measured
    # nothing; real agent sessions are far longer.
    import json
    from pathlib import Path

    data = Path(__file__).resolve().parents[2] / "benchmarks" / "beakr_bench" / "data"
    for path in data.glob("*.json"):
        payload = json.loads(path.read_text())
        cases = payload["cases"] if isinstance(payload, dict) else payload
        for case in cases:
            for step in case.get("steps", []):
                if step.get("op") == "import_conversation":
                    chars = sum(len(m["content"]) for m in step["messages"])
                    assert chars >= 500, (path.name, case["id"], chars)
