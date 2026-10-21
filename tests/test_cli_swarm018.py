"""CLI fixes from swarm 018: six agents writing one project through the CLI.

Root causes guarded, one per test group:

- ``api knowledge run-wiki-write`` took its project only as ``--scope`` while
  every other command takes ``-P``/``--project``; its help listed a field under
  the actions it named (``--reason`` only for edit_rows) with no per-action
  index, so ``flag``'s page, concern and reason were guesswork; and the
  proposal it staged was only in the JSON (S18-4, S18-7).
- ``kb propose create --sections`` ran the CLI's own validator for the older
  content-plus-markers shape, which asked for ``<!-- sec:ID -->`` markers and
  section ids although Beakr writes both, then reported a second, misleading
  "inline citations missing from sections" error (S18-5, S18-23).
- That validator refused ``stance: "supports"`` (S18-6).
- ``kb rows --attribute Enrollment`` also answered "Enrollment target
  rationale" rows: the door's default match takes names holding the word
  (S18-11).
- ``kb entities`` printed its per-type counts on a terminal only, and
  ``kb rows`` had no ``--view`` for the door's conflicts view (S18-16).
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.citations import CitationValidationError, validate_proposal_sections
from beakr_cli.main import internal_app as app

API = "https://api.test"
PROJECT = "4dd25154-0d80-44ab-b4c2-167319bfacb4"
PROPOSAL = "1171c0a4-c839-486e-ba81-28b1a2445b80"


@pytest.fixture(autouse=True)
def api_env(isolated_home, monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


def _door(text: str = "ok", *, status: str = "success", **extra) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "content": [{"type": "text", "text": text}],
            "outcome": {"status": status, "error_code": extra.pop("error_code", None)},
            **extra,
        },
    )


def _bodies(route) -> list[dict]:
    return [json.loads(call.request.content) for call in route.calls]


# --- api knowledge run-wiki-write (S18-4, S18-7) ------------------------------


@pytest.mark.parametrize("option", ["-P", "--project", "--scope"])
@respx.mock
def test_run_wiki_write_takes_the_project_as_every_command_does(option: str) -> None:
    route = respx.post(f"{API}/v1/knowledge/wiki/write").mock(
        return_value=_door("Flagged.", proposal={"id": PROPOSAL, "content_hash": "abc"})
    )

    result = CliRunner().invoke(
        app,
        [
            "api",
            "knowledge",
            "run-wiki-write",
            "--action",
            "flag",
            "--page",
            "HX-201-201",
            "--concern",
            "contradiction",
            "--reason",
            "Decide whether enrollment on 5 Oct was 118 or 115.",
            option,
            PROJECT,
        ],
    )

    assert result.exit_code == 0, result.output
    assert _bodies(route)[0]["scope"] == PROJECT
    # stdout stays the JSON; the id to accept is named on stderr as well.
    assert json.loads(result.stdout)["proposal"]["id"] == PROPOSAL
    assert f"Proposal: {PROPOSAL}" in result.stderr


def test_run_wiki_write_help_lists_each_actions_fields() -> None:
    result = CliRunner().invoke(app, ["api", "knowledge", "run-wiki-write", "--help"])

    assert result.exit_code == 0, result.output
    text = " ".join(result.output.split())
    assert "flag: page, concern, reason, details?" in text
    assert "edit_rows: page, table, old_rows, rows" in text
    assert "-P, --scope, --project" in text


def test_project_alias_reaches_the_project_id_of_other_operations() -> None:
    from beakr_cli.commands.api import build_command, operations

    entry = next(op for op in operations() if op["op"] == "GET /v1/knowledge/wiki/pages")
    names = {opt for param in build_command(entry).params for opt in param.opts}

    assert {"--project-id", "-P", "--project"} <= names


# --- kb propose create (S18-5, S18-6, S18-23) ---------------------------------


def _staged_proposal() -> dict:
    return {
        "id": PROPOSAL,
        "proposal_type": "create",
        "status": "pending",
        "page_title": "VB-77",
        "content_hash": "hash-1",
    }


@respx.mock
def test_propose_create_sends_the_page_as_sections_without_markers_or_ids() -> None:
    write = respx.post(f"{API}/v1/knowledge/wiki/write").mock(
        return_value=_door("Proposal id: x", proposal={"id": PROPOSAL, "content_hash": "hash-1"})
    )
    show = respx.get(f"{API}/v1/knowledge/wiki/proposals/{PROPOSAL}").mock(
        return_value=httpx.Response(200, json=_staged_proposal())
    )
    sections = [
        {
            "title": "Description",
            "body": "VB-77 is Veritas Bio's NLRP3 inhibitor. {{conversation:ci-r1-1}}",
            "citations": [
                {
                    "key": "conversation:ci-r1-1",
                    "source_type": "conversation",
                    "source_title": "CI research",
                    "stance": "supports",
                    "meta": {"excerpt": "Veritas Bio's VB-77 is an NLRP3 inhibitor."},
                }
            ],
        }
    ]

    result = CliRunner().invoke(
        app,
        [
            "kb",
            "propose",
            "create",
            "--title",
            "VB-77",
            "--sections",
            json.dumps(sections),
            "--project",
            PROJECT,
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Invalid --sections" not in result.output
    body = _bodies(write)[0]
    assert body["action"] == "new"
    assert body["scope"] == PROJECT
    assert body["title"] == "VB-77"
    assert "content" not in body
    assert body["rationale"]
    assert body["sections"][0]["body"] == sections[0]["body"]
    # "supports" is sent as the stance the engine stores.
    assert body["sections"][0]["citations"][0]["stance"] == "support"
    assert show.called
    assert PROPOSAL in result.stdout


@respx.mock
def test_propose_create_content_is_the_body_of_its_one_section() -> None:
    write = respx.post(f"{API}/v1/knowledge/wiki/write").mock(
        return_value=_door("Proposal id: x", proposal={"id": PROPOSAL, "content_hash": "hash-1"})
    )
    respx.get(f"{API}/v1/knowledge/wiki/proposals/{PROPOSAL}").mock(
        return_value=httpx.Response(200, json=_staged_proposal())
    )
    content = "Ortiz became CEO on 15 Mar 2026. {{conversation:people-r2-1}}"
    citation = {
        "key": "conversation:people-r2-1",
        "source_type": "conversation",
        "source_title": "Board call",
        "meta": {"excerpt": "Lena Ortiz took over as CEO on 15 March."},
    }

    result = CliRunner().invoke(
        app,
        [
            "kb",
            "propose",
            "create",
            "--title",
            "Lena Ortiz",
            "--content",
            content,
            "--sections",
            json.dumps([{"title": "Description", "citations": [citation]}]),
            "--project",
            PROJECT,
        ],
    )

    assert result.exit_code == 0, result.output
    assert "missing from sections" not in result.output
    sent = _bodies(write)[0]["sections"]
    assert sent == [{"title": "Description", "citations": [citation], "body": content}]


@respx.mock
def test_propose_create_reports_an_engine_refusal_once() -> None:
    refusal = "Cite only your conversation, a file you uploaded, or a source Knowledge cites."
    respx.post(f"{API}/v1/knowledge/wiki/write").mock(
        return_value=_door(refusal, status="error", error_code="citation_not_citeable")
    )

    result = CliRunner().invoke(
        app,
        [
            "kb",
            "propose",
            "create",
            "--title",
            "VB-77",
            "--sections",
            json.dumps([{"title": "Description", "body": "Text {{doc:nope}}"}]),
            "--project",
            PROJECT,
        ],
    )

    assert result.exit_code == 2, result.output
    assert " ".join(result.output.split()).count(refusal) == 1
    assert "Invalid --sections" not in result.output
    assert "sec:" not in result.output


def test_a_section_without_an_id_is_reported_once() -> None:
    content = "<!-- sec:sec_description -->\nOrtiz is CEO. {{conversation:people-r2-1}}"
    sections = [
        {
            "citations": [
                {
                    "key": "conversation:people-r2-1",
                    "source_type": "conversation",
                    "source_title": "Board call",
                    "meta": {"excerpt": "Ortiz is CEO."},
                }
            ]
        }
    ]

    with pytest.raises(CitationValidationError) as exc:
        validate_proposal_sections(content, sections, require_content=True)

    assert "id is required" in str(exc.value)
    assert "missing from sections" not in str(exc.value)


@pytest.mark.parametrize(
    ("stance", "allowed"),
    [("supports", True), ("Contradict", True), ("qualify", True), ("maybe", False)],
)
def test_stance_aliases_are_read_as_the_engine_spells_them(stance: str, allowed: bool) -> None:
    content = "<!-- sec:sec_a -->\nA claim. {{agent_note:a}}"
    citation = {
        "key": "agent_note:a",
        "source_type": "agent_note",
        "source_title": "Note",
        "stance": stance,
        "meta": {"excerpt": "A claim."},
    }
    sections = [{"id": "sec_a", "citations": [citation]}]

    if allowed:
        validate_proposal_sections(content, sections, require_content=True)
    else:
        with pytest.raises(CitationValidationError, match="stance must be one of"):
            validate_proposal_sections(content, sections, require_content=True)


# --- kb rows (S18-11, S18-16) -------------------------------------------------


@respx.mock
def test_rows_attribute_asks_for_the_exact_name_first() -> None:
    route = respx.post(f"{API}/v1/knowledge/wiki/command").mock(
        return_value=_door("| HX-201-201 | Enrollment | 115 of 150 |")
    )

    result = CliRunner().invoke(app, ["kb", "rows", "--attribute", "Enrollment", "-P", PROJECT])

    assert result.exit_code == 0, result.output
    assert [body.get("match") for body in _bodies(route)] == ["exact"]
    assert "115 of 150" in result.output


@respx.mock
def test_rows_attribute_falls_back_to_the_default_match_when_no_field_has_that_name() -> None:
    route = respx.post(f"{API}/v1/knowledge/wiki/command").mock(
        side_effect=[
            _door("0 rows: none of the 16 card(s) ...", status="empty"),
            _door("| HX-201-201 | Enrollment | 115 of 150 |"),
        ]
    )

    result = CliRunner().invoke(app, ["kb", "rows", "--attribute", "Enrolment", "-P", PROJECT])

    assert result.exit_code == 0, result.output
    assert [body.get("match") for body in _bodies(route)] == ["exact", None]
    assert "115 of 150" in result.output
    assert "0 rows" not in result.output


@respx.mock
def test_rows_match_given_is_sent_as_given_once() -> None:
    route = respx.post(f"{API}/v1/knowledge/wiki/command").mock(return_value=_door())

    result = CliRunner().invoke(
        app, ["kb", "rows", "--attribute", "Enrollment", "--match", "similar", "-P", PROJECT]
    )

    assert result.exit_code == 0, result.output
    assert [body.get("match") for body in _bodies(route)] == ["similar"]


@respx.mock
def test_rows_view_conflicts_is_sent_to_the_door() -> None:
    route = respx.post(f"{API}/v1/knowledge/wiki/command").mock(
        return_value=_door("12 contradiction(s) on 16 card(s)")
    )

    result = CliRunner().invoke(app, ["kb", "rows", "--view", "conflicts", "-P", PROJECT])

    assert result.exit_code == 0, result.output
    assert _bodies(route)[0]["view"] == "conflicts"
    assert "12 contradiction(s)" in result.output


def test_rows_view_is_checked() -> None:
    result = CliRunner().invoke(app, ["kb", "rows", "--view", "conflict"])

    assert result.exit_code == 2
    assert "--view must be one of" in result.output


@respx.mock
def test_entities_piped_leads_with_the_count_per_type() -> None:
    pages = [
        {"id": "p1", "title": "Ana Ruiz", "page_type": "person"},
        {"id": "p2", "title": "Ashford Synthesis", "page_type": "organization"},
        {"id": "p3", "title": "Corvane Chemicals", "page_type": "organization"},
    ]
    respx.get(f"{API}/v1/knowledge/wiki/pages").mock(
        return_value=httpx.Response(200, json={"data": pages, "has_more": False})
    )

    result = CliRunner().invoke(app, ["kb", "entities", "-P", PROJECT])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[0] == "Types: organization 2  person 1"
    assert "Ashford Synthesis" in result.stdout
