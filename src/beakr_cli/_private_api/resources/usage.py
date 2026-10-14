"""Model usage and trace, over unlisted routes.

``usage`` groups model calls (``calls``, ``cost_usd``, ``tokens``) by key,
project, model or day; ``scope="org"`` needs an organization admin. Usage is
attributed to the API key or token that started the run (``key`` is ``None``
for web sessions and background work).

``trace`` answers how a page, output or run was made: inputs, runs, actors and
versions. ``trace.row`` answers it for one Knowledge value -- a single Facts,
Connections or Findings row of a card: its current cells, the sources it cites
with the version read, the commit and proposal that introduced and last changed
it (with who, ``via`` and the run), and its earlier values.

Example::

    report = beakr.usage.get(group_by="model", start="2026-09-01")
    page = beakr.trace.get("page", page_id)
    fact = beakr.trace.row(card_id, row="Temperature")
"""

from __future__ import annotations

from typing import Any, Literal

from beakr_sdk._resource import M
from beakr_sdk._typing import returns
from beakr_sdk.resources._base import Resource

from beakr_cli._private_api._generated import operations as ops

GroupBy = Literal["key", "project", "model", "day"]
TraceKind = Literal["page", "output", "run"]
RowTable = Literal["facts", "connections", "findings"]


class Usage(Resource[M]):
    @returns(ops.build_usage_get_usage)
    def get(
        self,
        *,
        group_by: GroupBy = "day",
        start: str | None = None,
        end: str | None = None,
        scope: Literal["self", "org"] = "self",
    ) -> Any:
        """``{group_by, from, to, scope, data: [...], total}``. ``start``/``end`` are ISO dates."""
        return self._run(
            ops.build_usage_get_usage(
                group_by=group_by, scope=scope, from_=start or None, to=end or None
            )
        )


class Trace(Resource[M]):
    @returns(ops.build_trace_get_trace)
    def get(self, kind: TraceKind, item_id: str) -> Any:
        """``{kind, id, inputs, runs, actors, versions, ...}`` for a page, output or run."""
        return self._run(ops.build_trace_get_trace(kind, item_id))

    @returns(ops.build_trace_get_trace)
    def row(
        self,
        card_id: str,
        *,
        row: str | int,
        table: RowTable = "facts",
        value: str | None = None,
    ) -> Any:
        """How one card row (a Knowledge value) was made.

        ``row`` is the row's 1-based position in the card's ``table`` or its
        Attribute, Relation or Finding text; ``value`` narrows a text match.
        Returns ``{kind: "row", value, row, inputs, introduced_by,
        last_changed_by, history, runs, actors, ...}``.
        """
        return self._run(
            ops.build_trace_get_trace("row", card_id, table=table, row=str(row), value=value)
        )
