"""One rule turns what was typed into a project, for every command and the stdio MCP tools.

Root cause (sandbox run, 2026-11-14): ``project_ref.find_project`` matched a
slug before a name and returned the first case-folded name match. With two
projects named ``TEST`` (slug ``test``) and ``test`` (slug ``test-2``),
``beakr projects show test`` answered ``TEST`` and ``beakr projects show TEST``
answered ``test``; with two projects both named ``My first project``,
``beakr kb ls -P "My first project"`` read whichever was listed first and said
nothing. The rule now is the hosted server's: an id, then the exact-case name,
then one case-insensitive name or slug match; several is a refusal naming each
candidate, none a refusal naming the closest names.
"""

from __future__ import annotations

import asyncio

import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.main import app
from beakr_cli.project_ref import ProjectNotResolved, pick_project

API = "https://api.test"
UPPER = "11111111-1111-4111-8111-111111111111"
LOWER = "22222222-2222-4222-8222-222222222222"
FIRST_A = "33333333-3333-4333-8333-333333333333"
FIRST_B = "44444444-4444-4444-8444-444444444444"
DOCS = "55555555-5555-4555-8555-555555555555"
PROJECTS = [
    # Listed first, with the slug the other project's name spells.
    {"id": UPPER, "name": "TEST", "slug": "test"},
    {"id": LOWER, "name": "test", "slug": "test-2"},
    {"id": FIRST_A, "name": "My first project", "slug": "my-first-project"},
    {"id": FIRST_B, "name": "My first project", "slug": "my-first-project-2"},
    {"id": DOCS, "name": "Platform Docs", "slug": "platform-docs"},
]
runner = CliRunner()

pytestmark = pytest.mark.real_project_refs


@pytest.fixture(autouse=True)
def authenticated(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")
    monkeypatch.setenv("COLUMNS", "400")
    from beakr_cli import mcp_tools_platform, project_ref

    monkeypatch.setattr(project_ref, "_resolved", {})
    monkeypatch.setattr(mcp_tools_platform, "_resolved_projects", {})


def _projects() -> respx.Route:
    return respx.get(f"{API}/v1/projects").respond(
        json={"data": PROJECTS, "has_more": False, "next_cursor": None}
    )


def _flat(text: str) -> str:
    return " ".join(text.split())


# --- the rule itself -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        (UPPER, UPPER),
        # The exact-case name wins over another project's slug and over case folding.
        ("test", LOWER),
        ("TEST", UPPER),
        # One case-insensitive name, and one slug.
        ("platform docs", DOCS),
        ("platform-docs", DOCS),
        ("PLATFORM-DOCS", DOCS),
        ("test-2", LOWER),
        ("my-first-project-2", FIRST_B),
    ],
)
def test_an_id_then_the_exact_name_then_one_loose_match(ref, expected):
    assert pick_project(PROJECTS, ref)["id"] == expected


def test_a_name_in_neither_case_that_two_projects_match_is_ambiguous():
    # "Test" is the exact name of neither; it matches both names without case
    # (and one slug): no candidate is chosen for the caller.
    with pytest.raises(ProjectNotResolved) as refused:
        pick_project(PROJECTS, "Test")
    assert refused.value.ambiguous
    assert refused.value.candidates == [f"TEST (test, {UPPER})", f"test (test-2, {LOWER})"]


def test_a_slug_and_another_projects_name_matching_is_ambiguous():
    projects = [
        {"id": UPPER, "name": "Alpha", "slug": "beta"},
        {"id": LOWER, "name": "BETA", "slug": "beta-2"},
    ]
    with pytest.raises(ProjectNotResolved) as refused:
        pick_project(projects, "beta")
    assert refused.value.ambiguous
    assert len(refused.value.candidates) == 2


def test_a_shared_exact_name_is_ambiguous_and_names_each_candidate():
    with pytest.raises(ProjectNotResolved) as refused:
        pick_project(PROJECTS, "My first project")
    assert refused.value.ambiguous
    assert refused.value.candidates == [
        f"My first project (my-first-project, {FIRST_A})",
        f"My first project (my-first-project-2, {FIRST_B})",
    ]


def test_no_match_names_the_closest_by_the_engines_matcher():
    with pytest.raises(ProjectNotResolved) as refused:
        pick_project(PROJECTS, "Platform Doc")
    assert not refused.value.ambiguous
    assert refused.value.candidates == [f"Platform Docs (platform-docs, {DOCS})"]
    # The engine's cutoff (0.6): a name this far off is not offered (0.4 offered it).
    with pytest.raises(ProjectNotResolved) as far:
        pick_project(PROJECTS, "Plumbing")
    assert far.value.candidates == []


# --- every place the CLI resolves a project ------------------------------------------


@respx.mock
@pytest.mark.parametrize(("ref", "expected"), [("test", LOWER), ("TEST", UPPER)])
def test_projects_show_reads_the_project_spelled_as_typed(ref, expected):
    _projects()
    shown = respx.get(f"{API}/v1/projects/{expected}").respond(
        json={"id": expected, "name": ref, "slug": "x"}
    )
    result = runner.invoke(app, ["projects", "show", ref, "--json"])
    assert result.exit_code == 0, result.output
    assert shown.called


@respx.mock
def test_projects_show_refuses_an_ambiguous_name_with_each_candidate():
    _projects()
    result = runner.invoke(app, ["projects", "show", "My first project"])
    assert result.exit_code == 2, result.output
    output = _flat(result.output)
    assert f"My first project (my-first-project, {FIRST_A})" in output
    assert f"My first project (my-first-project-2, {FIRST_B})" in output


@respx.mock
def test_projects_show_names_the_closest_when_nothing_matches():
    _projects()
    result = runner.invoke(app, ["projects", "show", "Platform Doc"])
    assert result.exit_code == 4, result.output
    assert f"Did you mean: Platform Docs (platform-docs, {DOCS})?" in _flat(result.output)


@respx.mock
@pytest.mark.parametrize(("ref", "expected"), [("test", LOWER), ("TEST", UPPER)])
def test_the_project_option_reads_the_project_spelled_as_typed(ref, expected):
    _projects()
    pages = respx.get(f"{API}/v1/knowledge/wiki/pages").respond(
        json={"data": [], "has_more": False, "next_cursor": None}
    )
    result = runner.invoke(app, ["kb", "ls", "-P", ref, "--json"])
    assert result.exit_code == 0, result.output
    assert pages.calls[0].request.url.params["project_id"] == expected


@respx.mock
def test_the_project_option_refuses_a_name_two_projects_share():
    # It exited 0 with an empty listing of whichever project was listed first.
    _projects()
    pages = respx.get(f"{API}/v1/knowledge/wiki/pages").respond(
        json={"data": [], "has_more": False, "next_cursor": None}
    )
    result = runner.invoke(app, ["kb", "ls", "-P", "My first project"])
    assert result.exit_code == 2, result.output
    assert not pages.called
    output = _flat(result.output)
    assert "2 projects are named 'My first project'" in output
    assert FIRST_A in output and FIRST_B in output


@respx.mock
def test_the_default_project_from_the_environment_uses_the_same_rule(monkeypatch):
    _projects()
    pages = respx.get(f"{API}/v1/knowledge/wiki/pages").respond(
        json={"data": [], "has_more": False, "next_cursor": None}
    )
    monkeypatch.setenv("BEAKR_PROJECT_ID", "test")
    result = runner.invoke(app, ["kb", "ls", "--json"])
    assert result.exit_code == 0, result.output
    assert pages.calls[0].request.url.params["project_id"] == LOWER

    monkeypatch.setenv("BEAKR_PROJECT_ID", "My first project")
    refused = runner.invoke(app, ["kb", "ls", "--json"])
    assert refused.exit_code == 2, refused.output
    assert "2 projects are named" in _flat(refused.output)


@respx.mock
def test_init_binds_the_project_spelled_as_typed_and_refuses_an_ambiguous_one(tmp_path):
    _projects()
    respx.get(f"{API}/v1/me/organization").respond(json={"id": "org-1"})
    bound = runner.invoke(app, ["init", "--project", "test", "--json"])
    assert bound.exit_code == 0, bound.output
    assert f'"project_id": "{LOWER}"' in bound.output

    refused = runner.invoke(app, ["init", "--project", "Test"])
    assert refused.exit_code == 2, refused.output


@respx.mock
def test_the_stdio_mcp_tools_resolve_by_the_same_rule():
    from beakr_cli import mcp_tools_platform
    from beakr_cli.mcp_tools_platform import ScopeNotFound

    _projects()

    assert asyncio.run(mcp_tools_platform._project_id("test")) == LOWER
    assert asyncio.run(mcp_tools_platform._project_id("TEST")) == UPPER
    assert asyncio.run(mcp_tools_platform._project_id("platform-docs")) == DOCS
    with pytest.raises(ScopeNotFound) as shared:
        asyncio.run(mcp_tools_platform._project_id("My first project"))
    assert shared.value.fields["error_code"] == "scope_ambiguous"
    assert FIRST_A in shared.value.fields["fix"] and FIRST_B in shared.value.fields["fix"]
    with pytest.raises(ScopeNotFound) as missing:
        asyncio.run(mcp_tools_platform._project_id("Platform Doc"))
    assert missing.value.fields["error_code"] == "scope_not_found"
    assert f"Platform Docs (platform-docs, {DOCS})" in missing.value.fields["fix"]


@respx.mock
@pytest.mark.parametrize(
    "argv", [["projects", "rm", "platform docs", "--yes"], ["projects", "archive", "platform docs"]]
)
def test_a_destructive_command_acts_only_on_an_exact_unique_name_an_id_or_a_slug(argv):
    # `projects show "platform docs"` reads Platform Docs; deleting or archiving
    # it on a case-folded match would act on a guess (coordination with #159).
    _projects()
    deleted = respx.delete(url__regex=rf"{API}/v1/projects/.*").respond(204)
    patched = respx.patch(url__regex=rf"{API}/v1/projects/.*").respond(json={"id": DOCS})

    refused = runner.invoke(app, argv)

    assert refused.exit_code == 2, refused.output
    assert f"Platform Docs (platform-docs, {DOCS})" in _flat(refused.output)
    assert not deleted.called and not patched.called
    for ref in ("Platform Docs", "platform-docs", DOCS):
        acted = runner.invoke(app, [argv[0], argv[1], ref, *argv[3:], "--json"])
        assert acted.exit_code == 0, acted.output
    assert deleted.called or patched.called
