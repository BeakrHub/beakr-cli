"""What an upload does to a file already at its path: the fields sent on completion.

``POST /v1/uploads/{upload_id}/complete`` takes ``on_existing`` (``"version"``, the
server's default, or ``"new"``), ``written_by_agent`` and ``artifact_id`` (the file
to add the upload to as a version). The beakr-sdk the CLI locks (0.2.0) sends none
of them, so :func:`placed` adds them to the completion the SDK's own upload flow
builds. That works with any beakr-sdk: one that sends them itself is given the same
values. Drop it once the CLI locks a beakr-sdk whose upload methods take them
(beakr-python #123).

A completion the server refuses for its placement (:data:`REFUSED_PLACEMENT`)
leaves the upload open, so it can be completed again with other options:
:func:`placed` records the upload's id on the error (``details["upload_id"]``),
or aborts the upload when the caller will not come back to it.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from beakr_sdk._calls import Call, Flow
from beakr_sdk.errors import BeakrError

#: ``POST /v1/uploads/{upload_id}/complete``.
_COMPLETE = re.compile(r"/v1/uploads/(?P<upload_id>[^/]+)/complete")

#: Refusals of a completion's placement. Nothing was created and the upload stays
#: open: several files of that name are in the folder (``ambiguous_path``), the
#: file there is one the caller cannot change (``file_not_writable``), or
#: ``artifact_id`` names no uploaded file in the project (``invalid_artifact``).
REFUSED_PLACEMENT = frozenset({"ambiguous_path", "file_not_writable", "invalid_artifact"})


def placement_fields(
    *,
    on_existing: str | None = None,
    written_by_agent: bool | None = None,
    version_of: str | None = None,
) -> dict[str, Any]:
    """The completion fields for these options; only those given, so the server's
    defaults (version a file at the same path, a person's upload) apply otherwise."""
    fields: dict[str, Any] = {}
    if on_existing is not None:
        fields["on_existing"] = on_existing
    if written_by_agent:
        fields["written_by_agent"] = True
    if version_of is not None:
        fields["artifact_id"] = version_of
    return fields


def refused_placement(exc: BaseException) -> bool:
    """Whether ``exc`` is the server refusing a completion's placement."""
    return isinstance(exc, BeakrError) and exc.code in REFUSED_PLACEMENT


def placed(flow: Flow, fields: dict[str, Any], *, abort_refused: bool = False) -> Flow:
    """Run the upload ``flow``, adding ``fields`` to the body of its completion.

    A placement refusal is raised as the SDK raised it, with the open upload's id
    in ``details["upload_id"]``; with ``abort_refused`` the upload is aborted
    first (``DELETE /v1/uploads/{upload_id}``), so an open upload nobody will
    complete does not count against the caller's open uploads.
    """
    try:
        step = next(flow)
    except StopIteration as done:
        return done.value
    while True:
        completing = None
        if isinstance(step, Call) and step.method == "POST":
            match = _COMPLETE.fullmatch(step.path)
            if match is not None:
                completing = match.group("upload_id")
                if fields and isinstance(step.json, dict):
                    step = replace(step, json={**step.json, **fields})
        try:
            result = yield step
        except GeneratorExit:
            flow.close()
            raise
        except BaseException as exc:  # noqa: BLE001 - handed back to the flow below
            if completing is not None and refused_placement(exc):
                exc.details.setdefault("upload_id", completing)  # type: ignore[attr-defined]
                if abort_refused:
                    try:
                        yield Call("DELETE", f"/v1/uploads/{completing}")
                    except BeakrError:
                        pass
            try:
                step = flow.throw(exc)
            except StopIteration as done:
                return done.value
            continue
        try:
            step = flow.send(result)
        except StopIteration as done:
            return done.value
