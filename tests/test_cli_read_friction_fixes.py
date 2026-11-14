"""The CLI's read-friction rows from the fdoctant run against engine #2745 (14 Nov).

- a ``kb`` read's Sources list leaves out the Knowledge pages the text already names,
  as the MCP tools' default list does (a ``sections`` reply listed every hit again);
- a project name two projects share is refused with both ids, as the MCP tools refuse
  it (``scope_ambiguous``), instead of reading one of them silently;
  ``projects show/archive/rm`` and ``projects members`` refuse it too (rm and archive
  acted on the first of the two);
- an empty ``kb ls`` says so;
- ``kb cat --outline`` reads as lines, and the door's ``ledger`` hint is the
  ``--ledger`` command.
"""

from __future__ import annotations

import json

import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.main import app

API = "https://api.test"
PAGE = "d1f688b7-238a-436d-b9bb-d9b875348c25"
PROJECT = "2de9e334-1bc2-4400-ba93-a2fba47e03ee"
TWIN_A = "03e0bf87-c493-481f-8399-f24f2172497a"
TWIN_B = "56100fcd-8dcf-4844-81cd-f19852340430"

runner = CliRunner()


@pytest.fixture(autouse=True)
def api(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


def _page_source(title: str, section: str) -> dict:
    return {
        "citation_key": f"wiki:{title}-{section}",
        "name": f"{title} › {section}",
        "source_type": "wiki",
        "kind": "wiki",
        "source_ref": f"beakr-source:v1:wiki:{title}#{section}",
    }


@respx.mock
def test_sections_sources_leave_out_the_pages_the_text_names():
    respx.post(f"{API}/v1/knowledge/wiki/command").respond(
        json={
            "content": [{"type": "text", "text": "1. [[Quokka notes]] › Overview\n   titre"}],
            "sources": [
                {
                    "citation_key": "artifact:v1",
                    "filename": "runtime.md",
                    "provider": "upload",
                    "location": {"file_id": "f1"},
                    "evidence": {"excerpt": "The titre is 4."},
                },
                _page_source("Quokka notes", "Overview"),
            ],
            "outcome": {"status": "success"},
        }
    )

    result = runner.invoke(app, ["kb", "sections", "titre", "-P", PROJECT])

    assert result.exit_code == 0, result.output
    assert "Sources (1 document):" in result.stdout
    assert "Quokka notes › Overview · source_ref" not in result.stdout
    assert '"The titre is 4."' in result.stdout


@pytest.mark.real_project_refs
@respx.mock
def test_a_name_two_projects_share_is_refused_with_both_ids():
    respx.get(f"{API}/v1/projects").respond(
        json={
            "data": [
                {"id": TWIN_A, "name": "iso fr twin", "slug": "iso-fr-twin"},
                {"id": TWIN_B, "name": "iso fr twin", "slug": "iso-fr-twin-2"},
            ],
            "has_more": False,
        }
    )
    door = respx.post(f"{API}/v1/knowledge/wiki/command")

    for argv in (["kb", "ls", "-P", "iso fr twin"], ["kb", "sections", "q", "-P", "iso fr twin"]):
        result = runner.invoke(app, argv)
        assert result.exit_code == 2, (argv, result.output)
        said = " ".join(result.output.split())
        assert "2 projects are named 'iso fr twin'" in said
        assert TWIN_A[:8] in said and TWIN_B[:8] in said
    assert not door.called


@pytest.mark.real_project_refs
@respx.mock
def test_a_slug_still_picks_one_of_the_twins():
    respx.get(f"{API}/v1/projects").respond(
        json={
            "data": [
                {"id": TWIN_A, "name": "iso fr twin", "slug": "iso-fr-twin"},
                {"id": TWIN_B, "name": "iso fr twin", "slug": "iso-fr-twin-2"},
            ],
            "has_more": False,
        }
    )
    door = respx.post(f"{API}/v1/knowledge/wiki/command").respond(
        json={"content": [{"type": "text", "text": "none"}], "outcome": {"status": "success"}}
    )

    result = runner.invoke(app, ["kb", "sections", "q", "-P", "iso-fr-twin-2"])

    assert result.exit_code == 0, result.output
    assert json.loads(door.calls.last.request.content)["scope"] == TWIN_B


_TWINS = {
    "data": [
        {"id": TWIN_A, "name": "iso fr twin", "slug": "iso-fr-twin"},
        {"id": TWIN_B, "name": "iso fr twin", "slug": "iso-fr-twin-2"},
    ],
    "has_more": False,
}


@respx.mock
@pytest.mark.parametrize(
    "argv",
    [
        ["projects", "rm", "iso fr twin", "--yes"],
        ["projects", "archive", "iso fr twin"],
        ["projects", "show", "iso fr twin"],
        ["projects", "members", "ls", "iso fr twin"],
    ],
)
def test_project_commands_refuse_a_name_two_projects_share(argv):
    respx.get(f"{API}/v1/projects").respond(json=_TWINS)
    deleted = respx.delete(url__regex=rf"{API}/v1/projects/.*")
    patched = respx.patch(url__regex=rf"{API}/v1/projects/.*")

    result = runner.invoke(app, argv)

    assert result.exit_code == 2, result.output
    said = " ".join(result.output.split())
    assert "2 projects are named 'iso fr twin'" in said
    assert TWIN_A in said.replace(" ", "") and TWIN_B in said.replace(" ", "")
    assert not deleted.called and not patched.called


@respx.mock
@pytest.mark.parametrize("ref", [TWIN_B, "iso-fr-twin-2"])
def test_projects_rm_acts_on_the_twin_an_id_or_slug_names(ref):
    respx.get(f"{API}/v1/projects").respond(json=_TWINS)
    other = respx.delete(f"{API}/v1/projects/{TWIN_A}").respond(204)
    deleted = respx.delete(f"{API}/v1/projects/{TWIN_B}").respond(204)

    result = runner.invoke(app, ["projects", "rm", ref, "--yes", "--json"])

    assert result.exit_code == 0, result.output
    assert deleted.called and not other.called


@respx.mock
@pytest.mark.parametrize("ref", [TWIN_B, "iso-fr-twin-2"])
def test_projects_archive_acts_on_the_twin_an_id_or_slug_names(ref):
    respx.get(f"{API}/v1/projects").respond(json=_TWINS)
    other = respx.patch(f"{API}/v1/projects/{TWIN_A}").respond(json={})
    patched = respx.patch(f"{API}/v1/projects/{TWIN_B}").respond(
        json={"id": TWIN_B, "name": "iso fr twin", "status": "archived"}
    )

    result = runner.invoke(app, ["projects", "archive", ref, "--json"])

    assert result.exit_code == 0, result.output
    assert patched.called and not other.called
    assert json.loads(patched.calls.last.request.content) == {"status": "archived"}


@respx.mock
def test_an_empty_project_listing_says_so():
    respx.get(f"{API}/v1/knowledge/wiki/pages").respond(
        json={"data": [], "has_more": False, "next_cursor": None}
    )

    result = runner.invoke(app, ["kb", "ls", "-P", PROJECT])

    assert result.exit_code == 0, result.output
    assert "No Knowledge pages here yet." in result.stdout


def _outline() -> dict:
    outline = {
        "title": "Quokka long section",
        "page_type": "topic",
        "revision": 3,
        "summary": "seed",
        "sections": [
            {
                "section_id": "overview",
                "title": "Overview",
                "chars": 100833,
                "citations": 2,
                "lines": "1-1802",
            }
        ],
        "edges": {"parent": "Quokka notes", "children": 0, "linked_from": {"index": 1}},
    }
    return {
        "content": [{"type": "text", "text": json.dumps(outline, indent=2)}],
        "outcome": {"status": "success"},
    }


@respx.mock
def test_cat_outline_reads_as_lines():
    respx.get(f"{API}/v1/knowledge/wiki/pages/{PAGE}").respond(
        json={"id": PAGE, "title": "Quokka long section", "project_id": PROJECT}
    )
    respx.post(f"{API}/v1/knowledge/wiki/command").respond(json=_outline())

    result = runner.invoke(app, ["kb", "cat", PAGE, "--outline"])

    assert result.exit_code == 0, result.output
    assert '"sections"' not in result.stdout
    assert "Quokka long section (topic, revision 3)" in result.stdout
    assert "- Overview (section_id overview): 100833 chars, 2 citations, lines 1-1802" in (
        result.stdout
    )
    assert "Edges: parent Quokka notes; 0 children; linked from index 1" in result.stdout


@respx.mock
def test_the_ledger_hint_is_the_ledger_command():
    respx.get(f"{API}/v1/knowledge/wiki/pages/{PAGE}").respond(
        json={"id": PAGE, "title": "Quokka long section", "project_id": PROJECT}
    )
    door = respx.post(f"{API}/v1/knowledge/wiki/command").respond(
        json={
            "content": [
                {
                    "type": "text",
                    "text": "Citation records: 0 current, 2 no longer cited by this text. "
                    "cat {page, section_id, ledger: true} returns them with their trust "
                    "fields.\n\nBody.",
                }
            ],
            "outcome": {"status": "success"},
        }
    )

    shown = runner.invoke(app, ["kb", "cat", PAGE, "--section-id", "overview"])

    assert shown.exit_code == 0, shown.output
    assert "cat {page, section_id, ledger: true}" not in shown.stdout
    assert "--ledger" in shown.stdout

    ledger = runner.invoke(app, ["kb", "cat", PAGE, "--section-id", "overview", "--ledger"])

    assert ledger.exit_code == 0, ledger.output
    assert json.loads(door.calls.last.request.content)["ledger"] is True
