"""This surface must not grow a private vocabulary again.

Every other surface that speaks the wiki vocabulary converged on two tools:
``knowledge_base`` and ``knowledge_base_write``. The agentic pullers gave up four
``wiki_*`` names, the Reorganizer gave up ``reparent_page`` and ``rename_page``,
the ingestion Compiler gave up ``find_parent``. Each was fixed the same way --
delete the private names, offer the shared tool -- because, as the engine's
vocabulary module puts it, *a private subset does not diverge loudly; it just
stops keeping up.*

This surface was the last holdout and the worst case, because it lives in a
different repository and no test in the engine could see it. It hand-mapped ~15
REST endpoints onto 25 invented ``kb_*`` names and covered 16 of the vocabulary's
operations; ``sections``, ``references``, ``ontology``, ``hover``, ``diff``,
``show``, ``diagnostics``, ``completions`` and ``suggest_parent`` were
unreachable from MCP and nothing reported it.

So the assertion is not "these tools exist" but "no tool here is a per-operation
name". A wrapper named for one command or one action is exactly how the drift
started, and it always looks harmless at the moment it is added.
"""

from __future__ import annotations

from beakr_cli import mcp_server

#: The two shared tools, spelled exactly as every other surface spells them.
SHARED_WIKI_TOOLS = {"knowledge_base", "knowledge_base_write"}

#: Tools that are legitimately this surface's own. None is a wiki operation:
#: research runs an agent, the profile/project ones read org configuration, the
#: proposal three are review actions (this surface's equivalent of the accept and
#: dismiss buttons in the product), and stats/graph read aggregate endpoints the
#: shared vocabulary has no command for. Listing proposals is NOT here: it is a
#: shared operation, reached as knowledge_base command='proposals'. The version
#: pair manages this installed package, not the wiki.
SURFACE_OWN_TOOLS = {
    "research",
    "list_projects",
    "get_profile",
    "show_proposal",
    "accept_proposal",
    "dismiss_proposal",
    "wiki_stats",
    "wiki_graph",
    "beakr_version",
    "update_beakr",
}


def _tool_names() -> set[str]:
    """Every @mcp.tool()-decorated coroutine in the server module."""
    import inspect

    return {
        name
        for name, obj in vars(mcp_server).items()
        if inspect.iscoroutinefunction(obj) and not name.startswith("_")
    }


def test_the_mcp_surface_is_the_shared_tools_plus_its_own() -> None:
    """The whole surface, in one assertion.

    Re-introducing a per-command wrapper shows up as a failing diff on this line
    rather than as an MCP client quietly unable to reach half the wiki.
    """
    assert _tool_names() == SHARED_WIKI_TOOLS | SURFACE_OWN_TOOLS


def test_no_tool_is_named_for_a_single_command_or_action() -> None:
    """No ``kb_cat``, no ``kb_propose_create``, no successor to either.

    Checked by shape rather than by an explicit ban-list, so a wrapper invented
    under a new prefix is caught too. The failure mode this prevents is not a
    broken call -- a wrapper works fine on the day it is written -- it is the
    wrapper still offering four commands two years after the shared tool grew to
    nineteen.
    """
    banned_prefixes = ("kb_", "wiki_read_", "wiki_write_", "knowledge_base_read_")
    offenders = {
        name
        for name in _tool_names()
        if name.startswith(banned_prefixes) and name not in SURFACE_OWN_TOOLS
    }
    assert not offenders, (
        f"per-operation MCP tools reintroduced: {sorted(offenders)}. "
        "Add a command to knowledge_base or an action to knowledge_base_write instead; "
        "a name here is a seventh vocabulary that nothing in the engine can see."
    )


def test_the_shared_tools_take_a_command_and_an_action() -> None:
    """Their first argument is the vocabulary's own selector, not a bespoke one."""
    import inspect

    assert list(inspect.signature(mcp_server.knowledge_base).parameters)[0] == "command"
    assert list(inspect.signature(mcp_server.knowledge_base_write).parameters)[0] == "action"


def test_scope_is_the_only_scoping_argument_on_the_shared_tools() -> None:
    """No project_id, and no personal_only.

    ``scope`` resolves a project by name or id server-side. Personal is not a
    special scope -- it is a project with project_type='personal' that
    list_projects reports -- so a second scoping parameter here would be a second
    vocabulary for the one concept, and the personal flag additionally cost a
    /v1/projects round trip plus a module-level cache to hide it.
    """
    import inspect

    for tool in (mcp_server.knowledge_base, mcp_server.knowledge_base_write):
        params = set(inspect.signature(tool).parameters)
        assert "scope" in params
        assert "project_id" not in params
        assert "personal_only" not in params
        assert "personal" not in params


def test_writes_cannot_select_the_direct_landing() -> None:
    """The proposal landing is bound by the route, never chosen by the caller.

    Every in-process surface binds ``direct_write`` at tool construction so a
    model cannot pick it at call time. Exposing it here would hand that choice to
    an external client, on the surface furthest from the runtime's trust
    boundary.
    """
    import inspect

    params = set(inspect.signature(mcp_server.knowledge_base_write).parameters)
    assert "direct_write" not in params
    assert "landing" not in params
