"""The caller's personal memories, over unlisted routes."""

from __future__ import annotations

from typing import Any

from beakr_sdk._resource import M
from beakr_sdk._typing import returns, returns_all
from beakr_sdk.resources._base import Resource

from beakr_cli._private_api._generated import operations as ops


class Memories(Resource[M]):
    @returns_all(ops.build_personal_memory_list_personal_memory)
    def list(self, *, kind: str | None = None, applies_to: str | None = None) -> Any:
        return self._list_all(
            ops.build_personal_memory_list_personal_memory(kind=kind, applies_to=applies_to)
        )

    @returns(ops.build_personal_memory_delete_personal_memory)
    def delete(self, memory_id: str) -> Any:
        return self._run(ops.build_personal_memory_delete_personal_memory(memory_id))
