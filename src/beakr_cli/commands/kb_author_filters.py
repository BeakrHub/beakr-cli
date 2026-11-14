"""The author filters of ``beakr kb log``, ``kb proposals ls`` and ``kb ls``.

The Knowledge door's ``log`` and ``proposals`` take ``by``, ``via``, ``kind``,
``accepted_by``, ``since``, ``until`` and ``offset``; its ``ls`` takes
``created_by``, ``created_via``, ``created_since``, ``changed_by``,
``changed_via`` and ``changed_since``. The REST routes those three commands
read without a filter take none of them, so a command given any filter reads
the door instead. This module holds the flags and turns them into the door's
arguments; an empty result means no filter was given and the command keeps its
unfiltered path.
"""

from __future__ import annotations

import typer

by_opt = typer.Option(
    None, "--by", help="Only changes by this person: a name, email address, user id or me."
)
via_opt = typer.Option(
    None, "--via", help="Only changes made through this client or agent (prefix), e.g. codex."
)
kind_opt = typer.Option(
    None, "--kind", help="Only changes by this kind of writer: person, agent, beakr or system."
)
accepted_by_opt = typer.Option(
    None, "--accepted-by", help="Only changes accepted by this person, or by auto-approve."
)
since_opt = typer.Option(
    None, "--since", help="Only changes on or after this date (YYYY-MM-DD, UTC) or ISO time."
)
until_opt = typer.Option(
    None, "--until", help="Only changes on or before this date (YYYY-MM-DD, UTC) or ISO time."
)

created_by_opt = typer.Option(
    None, "--created-by", help="Only pages this person created: a name, email, user id or me."
)
created_via_opt = typer.Option(
    None, "--created-via", help="Only pages created through this client or agent (prefix)."
)
created_since_opt = typer.Option(
    None, "--created-since", help="Only pages created on or after this date or ISO time."
)
changed_by_opt = typer.Option(
    None, "--changed-by", help="Only pages this person changed: a name, email, user id or me."
)
changed_via_opt = typer.Option(
    None, "--changed-via", help="Only pages changed through this client or agent (prefix)."
)
changed_since_opt = typer.Option(
    None, "--changed-since", help="Only pages changed on or after this date or ISO time."
)


def _given(options: dict[str, str | None]) -> dict[str, object]:
    """The options that carry a value, trimmed, under the door's argument names."""
    return {name: value.strip() for name, value in options.items() if value and value.strip()}


def history_arguments(
    *,
    by: str | None,
    via: str | None,
    kind: str | None,
    accepted_by: str | None,
    since: str | None,
    until: str | None,
    offset: int | None,
) -> dict[str, object]:
    """The door arguments of a filtered ``log`` or ``proposals``; empty when none is given."""
    arguments = _given(
        {
            "by": by,
            "via": via,
            "kind": kind,
            "accepted_by": accepted_by,
            "since": since,
            "until": until,
        }
    )
    if offset:
        # Offset 0 is the first window, which the unfiltered read already is.
        arguments["offset"] = offset
    return arguments


def page_arguments(
    *,
    created_by: str | None,
    created_via: str | None,
    created_since: str | None,
    changed_by: str | None,
    changed_via: str | None,
    changed_since: str | None,
) -> dict[str, object]:
    """The door arguments of an ``ls`` filtered by author; empty when none is given."""
    return _given(
        {
            "created_by": created_by,
            "created_via": created_via,
            "created_since": created_since,
            "changed_by": changed_by,
            "changed_via": changed_via,
            "changed_since": changed_since,
        }
    )
