"""Conversation settings and freshness, over unlisted routes."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from beakr_sdk._resource import M
from beakr_sdk._typing import returns
from beakr_sdk.resources import conversations as _public

from beakr_cli._private_api._generated import operations as ops


class Conversations(_public.Conversations[M]):
    """:class:`beakr_sdk.resources.conversations.Conversations` plus the CLI's unlisted calls."""

    @returns(ops.build_conversations_get_conversation_settings)
    def get_settings(self) -> Any:
        """``{full_text_clients}``: the clients whose imports keep full text by default."""
        return self._run(ops.build_conversations_get_conversation_settings())

    @returns(ops.build_conversations_put_conversation_settings)
    def update_settings(self, *, full_text_clients: Sequence[str]) -> Any:
        """Choose the clients whose imports keep full text by default (per person, per org)."""
        return self._run(
            ops.build_conversations_put_conversation_settings(
                body={"full_text_clients": list(full_text_clients)}
            )
        )

    @returns(ops.build_conversations_freshness)
    def freshness(self, conversation_id: str) -> Any:
        """Whether the pages and documents an imported session read are still the
        versions it read: ``{fresh, coverage, changed: [{kind, ..., state}],
        unchecked_pages, unchecked_documents, unchecked_sources, unchecked_files,
        indexed}``. Reads declared at import are checked before any compounding."""
        return self._run(ops.build_conversations_freshness(conversation_id))
