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
HASH = "a" * 64


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


@pytest.mark.real_project_refs
@respx.mock
def test_api_project_option_resolves_a_project_name_to_its_id() -> None:
    respx.get(f"{API}/v1/projects").respond(
        json={
            "data": [{"id": PROJECT, "name": "Halcyon Swarm 018", "slug": "halcyon-swarm-018"}],
            "has_more": False,
            "next_cursor": None,
        }
    )
    pages = respx.get(f"{API}/v1/knowledge/wiki/pages").respond(
        json={"data": [], "has_more": False, "next_cursor": None}
    )

    result = CliRunner().invoke(
        app, ["api", "knowledge", "list-wiki-pages", "-P", "Halcyon Swarm 018"]
    )

    assert result.exit_code == 0, result.output
    assert pages.calls.last.request.url.params["project_id"] == PROJECT


@pytest.mark.real_project_refs
@respx.mock
def test_api_project_option_names_close_projects_for_an_unknown_one() -> None:
    respx.get(f"{API}/v1/projects").respond(
        json={
            "data": [{"id": PROJECT, "name": "Halcyon Swarm 018", "slug": "halcyon-swarm-018"}],
            "has_more": False,
            "next_cursor": None,
        }
    )
    pages = respx.get(f"{API}/v1/knowledge/wiki/pages")

    result = CliRunner().invoke(
        app, ["api", "knowledge", "list-wiki-pages", "--project", "Halcyon Swarm"]
    )

    assert result.exit_code == 2, result.output
    assert "Halcyon Swarm 018" in result.output
    assert not pages.called


# --- kb propose create (S18-5, S18-6, S18-23) ---------------------------------


def _staged_proposal() -> dict:
    return {
        "id": PROPOSAL,
        "proposal_type": "create",
        "status": "pending",
        "page_title": "VB-77",
        "content_hash": HASH,
    }


@respx.mock
def test_propose_create_sends_the_page_as_sections_without_markers_or_ids() -> None:
    write = respx.post(f"{API}/v1/knowledge/wiki/write").mock(
        return_value=_door("Proposal id: x", proposal={"id": PROPOSAL, "content_hash": HASH})
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
        return_value=_door("Proposal id: x", proposal={"id": PROPOSAL, "content_hash": HASH})
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


_AUTO_REPLY = (
    "Proposed new page 'VB-77'.\nProposal id: " + PROPOSAL + "\n"
    "Auto-approve is on for this user in this project, so this proposal is being "
    "applied automatically (usually within seconds). Do not ask the user to approve it "
    "and do not call accept_proposal.\n"
    "Citation debt: section Description has 1 claim with no citation.\n"
    "A page titled 'VB 77' already exists in this project.\n"
    "Page type 'drug' is not declared here; written as 'compound'."
)


def _invoke_create(*extra: str):
    return CliRunner().invoke(
        app,
        [
            "kb",
            "propose",
            "create",
            "--title",
            "VB-77",
            "--sections",
            json.dumps([{"title": "Description", "body": "VB-77 is an NLRP3 inhibitor."}]),
            "--project",
            PROJECT,
            *extra,
        ],
    )


@respx.mock
def test_propose_create_prints_the_door_reply_and_no_accept_hint_under_auto_approve() -> None:
    respx.post(f"{API}/v1/knowledge/wiki/write").mock(
        return_value=_door(_AUTO_REPLY, proposal={"id": PROPOSAL, "content_hash": HASH})
    )
    respx.get(f"{API}/v1/knowledge/wiki/proposals/{PROPOSAL}").mock(
        return_value=httpx.Response(200, json=_staged_proposal())
    )
    accept = respx.post(f"{API}/v1/knowledge/wiki/proposals/{PROPOSAL}/accept")

    result = _invoke_create()

    assert result.exit_code == 0, result.output
    # Piped, the reply is on stderr and stdout stays the proposal.
    for line in _AUTO_REPLY.splitlines()[2:]:
        assert " ".join(line.split()) in " ".join(result.stderr.split())
    assert "Auto-approve" not in result.stdout
    assert PROPOSAL in result.stdout
    assert f"beakr kb proposals accept {PROPOSAL}" not in result.output
    assert f"beakr kb proposals show {PROPOSAL}" in result.stdout
    assert not accept.called


@respx.mock
def test_propose_create_accept_when_auto_approve_applied_it_first_succeeds() -> None:
    respx.post(f"{API}/v1/knowledge/wiki/write").mock(
        return_value=_door(_AUTO_REPLY, proposal={"id": PROPOSAL, "content_hash": HASH})
    )
    # Pending when read back, accepted by the worker before our accept lands.
    respx.get(f"{API}/v1/knowledge/wiki/proposals/{PROPOSAL}").mock(
        side_effect=[
            httpx.Response(200, json=_staged_proposal()),
            httpx.Response(200, json={**_staged_proposal(), "status": "accepted"}),
        ]
    )
    accept = respx.post(f"{API}/v1/knowledge/wiki/proposals/{PROPOSAL}/accept").respond(
        400, json={"detail": "Proposal is accepted, not pending"}
    )

    result = _invoke_create("--accept", "--yes", "--json")

    assert result.exit_code == 0, result.output
    assert accept.called
    shown = json.loads(result.stdout)
    assert shown["status"] == "accepted"
    assert shown["accepted"] is True


@respx.mock
def test_propose_create_accept_still_fails_when_the_proposal_was_not_applied() -> None:
    respx.post(f"{API}/v1/knowledge/wiki/write").mock(
        return_value=_door("Proposal id: x", proposal={"id": PROPOSAL, "content_hash": HASH})
    )
    respx.get(f"{API}/v1/knowledge/wiki/proposals/{PROPOSAL}").mock(
        return_value=httpx.Response(200, json=_staged_proposal())
    )
    respx.post(f"{API}/v1/knowledge/wiki/proposals/{PROPOSAL}/accept").respond(
        400, json={"detail": "Proposal is dismissed, not pending"}
    )

    result = _invoke_create("--accept", "--yes", "--json")

    assert result.exit_code == 1, result.output


@respx.mock
def test_propose_create_refuses_a_parent_that_names_no_page() -> None:
    respx.get(f"{API}/v1/knowledge/wiki/pages/by-title").respond(
        404,
        json={
            "error": {
                "code": "not_found",
                "message": "Wiki page not found",
                "details": {"suggestions": ["Gout Flares Competitive Landscape"]},
            }
        },
    )
    write = respx.post(f"{API}/v1/knowledge/wiki/write")

    result = _invoke_create("--parent", "Gout Flare Competitive Landscape")

    assert result.exit_code == 4, result.output
    assert "Page not found: Gout Flare Competitive Landscape" in result.stderr
    assert '"Gout Flares Competitive Landscape"' in result.stderr
    assert not write.called


@respx.mock
def test_propose_create_sends_the_resolved_parent_id() -> None:
    parent_id = "5fd0e794-2b95-44bc-9185-36a53ab42868"
    respx.get(f"{API}/v1/knowledge/wiki/pages/by-title").respond(
        json={"id": parent_id, "title": "Gout Flares Competitive Landscape", "project_id": PROJECT}
    )
    write = respx.post(f"{API}/v1/knowledge/wiki/write").mock(
        return_value=_door("Proposal id: x", proposal={"id": PROPOSAL, "content_hash": HASH})
    )
    respx.get(f"{API}/v1/knowledge/wiki/proposals/{PROPOSAL}").mock(
        return_value=httpx.Response(200, json=_staged_proposal())
    )

    result = _invoke_create("--parent", "Gout Flares Competitive Landscape")

    assert result.exit_code == 0, result.output
    assert _bodies(write)[0]["parent"] == parent_id


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
    assert "No field is named exactly" not in result.stderr


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
    assert "115 of 150" in result.stdout
    assert "0 rows" not in result.output
    # The wider match is said, on stderr, so the reader knows the name was not exact.
    assert "No field is named exactly 'Enrolment'" in result.stderr


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
def test_entities_piped_counts_go_to_stderr_and_stdout_stays_tsv() -> None:
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
    # The counts go to stderr; stdout stays one TSV line per page for cut and read.
    assert "Types: organization 2  person 1" in result.stderr
    assert "Types:" not in result.stdout
    assert [line.split("\t")[0] for line in result.stdout.splitlines()] == ["p1", "p2", "p3"]
