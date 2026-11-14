"""A proposal named by the start of its id, as ``beakr kb proposals ls`` prints it.

The listing's ID column shows the first eight characters of each id, and the
hints say to pass "the proposal id" to ``show``, ``accept`` and ``dismiss``,
which took only the whole id (sandbox run, 2026-11-14). A prefix that names
exactly one proposal the caller can list is now that proposal; one that names
several is refused with each candidate, never guessed; a whole id is used as
given, without a request.
"""

from __future__ import annotations

import re
from typing import Any

import typer
from beakr_sdk.errors import NotFoundError

from beakr_cli.errors import EXIT_USAGE
from beakr_cli.output import err_console, page_rows

__all__ = ["resolve_proposal_id"]

_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_PREFIX = re.compile(r"[0-9a-fA-F-]+")
#: Characters a prefix needs: fewer names too many proposals to be a reference.
MIN_PREFIX = 4
#: Proposals read per status while looking for a prefix (pages of 200).
_PAGES = 5
_PAGE_SIZE = 200
#: Where a proposal can be: the pending ones first, since those are acted on.
_ALL_STATUSES = ("pending", "accepted", "dismissed", "superseded", "resolved")


def _listed(sdk: Any, status: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(_PAGES):
        params = {"status": status, "limit": _PAGE_SIZE}
        if cursor:
            params["cursor"] = cursor
        page = sdk.request("GET", "/v1/knowledge/wiki/proposals", params=params)
        rows += [row for row in page_rows(page) if isinstance(row, dict)]
        cursor = page.get("next_cursor") if isinstance(page, dict) else None
        if not cursor or not (isinstance(page, dict) and page.get("has_more")):
            break
    return rows


def _label(proposal: dict[str, Any]) -> str:
    title = " ".join(str(proposal.get("page_title") or "").split())
    kind = proposal.get("proposal_type") or "proposal"
    status = proposal.get("status") or ""
    return f"{proposal.get('id')} ({kind}, {status}{f', {title}' if title else ''})"


def resolve_proposal_id(sdk: Any, given: str, *, pending_only: bool = False) -> str:
    """The whole id of the proposal ``given`` names (an id, or a unique start of one).

    ``pending_only`` looks among pending proposals alone (accept and dismiss
    act on nothing else). Several matches is a usage error (exit 2) naming
    each; none is not found (exit 4).
    """
    given = given.strip()
    if _UUID.fullmatch(given) or not _PREFIX.fullmatch(given):
        # A whole id, or something that is not the start of one: the route answers.
        return given
    if len(given) < MIN_PREFIX:
        err_console.print(
            f"{given!r} is too short to name a proposal: give at least {MIN_PREFIX} characters "
            "of its id, as `beakr kb proposals ls` prints it.",
            markup=False,
        )
        raise typer.Exit(EXIT_USAGE)
    wanted = given.lower()
    matches: dict[str, dict[str, Any]] = {}
    for status in ("pending",) if pending_only else _ALL_STATUSES:
        for proposal in _listed(sdk, status):
            if str(proposal.get("id") or "").lower().startswith(wanted):
                matches.setdefault(str(proposal["id"]), {"status": status, **proposal})
    if len(matches) == 1:
        return next(iter(matches))
    if not matches:
        among = "pending proposal" if pending_only else "proposal"
        raise NotFoundError(
            f"No {among} has an id starting with {given!r}.",
            fix="List them with `beakr kb proposals ls` (add --status accepted for applied ones).",
        )
    err_console.print(
        f"{len(matches)} proposals have an id starting with {given!r}; give more of the id:",
        markup=False,
    )
    for proposal in matches.values():
        err_console.print(f"  {_label(proposal)}", markup=False, highlight=False, soft_wrap=True)
    raise typer.Exit(EXIT_USAGE)
