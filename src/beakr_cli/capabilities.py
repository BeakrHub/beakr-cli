"""What this Beakr installation provides, and the words for what it does not.

A self-hosted installation serves only part of the API: organization
members, external users, end-user tokens, service accounts, integrations and
skills are hosted features it leaves out on purpose, and it answers a bare
``404 {"detail": "Not Found"}`` for them (the reply of a route the server
does not have, as opposed to a record that is missing, which carries an error
envelope with its own message). The CLI and the MCP server used to print that
404 as it came, which reads like a bug.

Two signals say an area is missing, and both are read here:

- the server's own public OpenAPI document, through
  ``beakr_sdk``'s ``meta.capabilities()``, for the documented operations;
- a bare 404 from a call, for every route, documented or not.

Either way the user reads one sentence: "This Beakr installation does not
provide <area>."
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from beakr_sdk import Capabilities
    from beakr_sdk.errors import BeakrError

#: ``error_code`` of a refusal for a route this installation does not have.
NOT_PROVIDED_CODE = "not_provided"

#: Routes a command reads only to find what it acts on: the active organization,
#: read before any ``/v1/organizations/{org_id}/...`` call when no org is given.
#: When one is missing, the area is the command's own, not the lookup's.
LOOKUP_ROUTES = re.compile(r"^/v1/me/organization(/|$)")

#: Areas a Beakr installation may leave out, most specific prefix first: a
#: name for the sentence, the paths it covers, and one documented operation
#: that stands for it (``beakr doctor`` asks the capabilities about that one;
#: ``None`` for an area outside the documented contract).
AREAS: tuple[tuple[str, re.Pattern[str], tuple[str, str] | None], ...] = (
    (
        "organization members",
        re.compile(r"^/v1/organizations/[^/]+/members(/|$)"),
        ("GET", "/v1/organizations/{org_id}/members"),
    ),
    (
        "external users",
        re.compile(r"^/v1/organizations/[^/]+/external-users(/|$)"),
        ("GET", "/v1/organizations/{org_id}/external-users"),
    ),
    (
        "service accounts",
        re.compile(r"^/v1/organizations/[^/]+/service-accounts(/|$)"),
        ("GET", "/v1/organizations/{org_id}/service-accounts"),
    ),
    (
        "the organization audit log",
        re.compile(r"^/v1/organizations/[^/]+/audit(/|$)"),
        None,
    ),
    (
        "organization administration",
        re.compile(r"^/v1/organizations(/|$)"),
        ("GET", "/v1/organizations"),
    ),
    # A lookup: the command's own area names it better (``COMMAND_AREAS``).
    ("organization administration", LOOKUP_ROUTES, None),
    (
        "end-user tokens",
        re.compile(r"^/v1/auth/end-user-tokens(/|$)"),
        ("GET", "/v1/auth/end-user-tokens"),
    ),
    ("organization invitations", re.compile(r"^/v1/invitations(/|$)"), None),
    ("integrations", re.compile(r"^/v1/integrations(/|$)"), ("GET", "/v1/integrations")),
    ("skills", re.compile(r"^/v1/(me/)?skills(/|$)"), ("GET", "/v1/skills")),
    ("Knowledge statistics", re.compile(r"^/v1/knowledge/stats(/|$)"), None),
    ("the Knowledge graph", re.compile(r"^/v1/knowledge/wiki/graph(/|$)"), None),
    ("Knowledge profiles", re.compile(r"^/v1/knowledge/profiles(/|$)"), None),
)

#: The area of each org-scoped CLI command, by its leading words (longest first).
COMMAND_AREAS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("org", "service-accounts"), "service accounts"),
    (("org",), "organization administration"),
    (("members",), "organization members"),
    (("external-users",), "external users"),
)

#: The documented operations each MCP tool needs: a tool whose operation the
#: installation's document leaves out is not listed. Tools over routes outside
#: the documented contract are judged by their first call instead (a bare 404
#: hides them, see ``TieredFastMCP``).
TOOL_OPERATIONS: dict[str, tuple[tuple[str, str], ...]] = {
    "context": (("POST", "/v1/context"),),
    "list_projects": (("GET", "/v1/projects"),),
    "research": (("POST", "/v1/knowledge/research"),),
    "knowledge_base": (("POST", "/v1/knowledge/wiki/command"),),
    "knowledge_base_write": (("POST", "/v1/knowledge/wiki/write"),),
    "read_file": (("POST", "/v1/artifacts/read"),),
    "documents": (("POST", "/v1/knowledge/documents"),),
    "show_proposal": (("GET", "/v1/knowledge/wiki/proposals/{proposal_id}"),),
    "accept_proposal": (("POST", "/v1/knowledge/wiki/proposals/{proposal_id}/accept"),),
    "dismiss_proposal": (("POST", "/v1/knowledge/wiki/proposals/{proposal_id}/dismiss"),),
    "conversations_search": (("GET", "/v1/conversations"), ("GET", "/v1/conversations/search")),
    "import_conversation": (("POST", "/v1/conversations/import"),),
    "skills": (("GET", "/v1/skills"),),
}


def api_path(url_path: str) -> str:
    """The API path of a request URL's path (``/v1/...``), past any base-URL prefix."""
    index = url_path.find("/v1/")
    return url_path[index:] if index >= 0 else url_path


def request_of(response: Any) -> tuple[str, str]:
    """``(method, API path)`` of the request an ``httpx.Response`` answers (empty if unknown)."""
    try:
        request = response.request
    except RuntimeError:
        return "", ""
    return request.method, api_path(request.url.path)


def area_for(path: str, command: tuple[str, ...] = ()) -> str:
    """The name of the area ``path`` belongs to.

    ``command`` is the CLI command's words (``("members", "ls")``): for a
    lookup route it names the area the command was after, and for a route no
    area covers it names the command. Without one, such a route is named as
    it is.
    """
    path = api_path(path)
    if command and LOOKUP_ROUTES.match(path):
        for words, name in COMMAND_AREAS:
            if command[: len(words)] == words:
                return name
    for name, pattern, _ in AREAS:
        if pattern.match(path):
            return name
    if command:
        return f"what `beakr {' '.join(command)}` needs"
    return path or "this feature"


def not_provided_message(area: str) -> str:
    return f"This Beakr installation does not provide {area}."


def is_bare_not_found(response: Any) -> bool:
    """A 404 for a route the server does not have: ``{"detail": "Not Found"}``, no envelope.

    A missing record answers 404 too, but with an error envelope and its own
    message ("Project not found"); only the router's own reply is this bare.
    """
    if response is None or response.status_code != 404:
        return False
    try:
        body = response.json()
    except ValueError:
        return False
    return isinstance(body, dict) and body.get("detail") == "Not Found" and "error" not in body


def not_provided(exc: BeakrError, command: tuple[str, ...] = ()) -> str | None:
    """The "does not provide" sentence when ``exc`` is a bare 404, else None.

    Only a route the server does not have answers this bare, so no document
    needs to be read to tell: the reply itself says so. The SDK raises
    ``NotProvidedError`` for it, with the path and, for a method refused at its
    active-organization lookup (``LOOKUP_ROUTES``), the ``area`` the method was
    after (``members.list``: organization members). That area names a lookup
    refusal when no ``command`` does; every other refusal is named by
    :func:`area_for`, which names more areas than the SDK (``beakr org audit``:
    the organization audit log, where the SDK says organization
    administration). An older SDK raised a plain ``NotFoundError``, read here
    from its response.
    """
    from beakr_sdk.errors import NotProvidedError

    if isinstance(exc, NotProvidedError):
        if not exc.path:
            return not_provided_message(exc.area) if exc.area else exc.message
        if exc.area and not command and LOOKUP_ROUTES.match(api_path(exc.path)):
            return not_provided_message(exc.area)
        area = area_for(exc.path, command)
        # A route no area covers, with no command to name: the SDK's own
        # sentence names the operation (method and path).
        return exc.message if area == api_path(exc.path) else not_provided_message(area)
    response = getattr(exc, "response", None)
    if not is_bare_not_found(response):
        return None
    return not_provided_message(area_for(request_of(response)[1], command))


def unprovided_areas(capabilities: Capabilities) -> list[str]:
    """The documented areas this installation's document leaves out, in ``AREAS`` order."""
    return [
        name
        for name, _, operation in AREAS
        if operation is not None and not capabilities.serves(*operation)
    ]


def unserved_tools(capabilities: Capabilities, tools: list[str]) -> set[str]:
    """The tools in ``tools`` whose documented operations this installation does not serve."""
    return {
        tool
        for tool in tools
        if any(not capabilities.serves(*op) for op in TOOL_OPERATIONS.get(tool, ()))
    }


def describe_capabilities(capabilities: Capabilities) -> dict[str, Any]:
    """``beakr doctor``'s summary: whether the server describes itself, and what it lacks."""
    return {
        "documented": capabilities.documented,
        "unavailable": unprovided_areas(capabilities),
    }
