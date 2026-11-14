"""Confirm before entering a locked project: the API's ``409 session_lock_confirm``.

An automatic session that already read or wrote another project is held on its
first touch of a project whose boundary keeps its content in: entering it would
limit the session's writes to that project (or, as a second one, leave none).
Nothing is read, written or recorded. The same request with
``X-Beakr-Confirm-Lock: <project id>`` (several comma-separated) goes in, with the
usual lock notice (:mod:`beakr_cli.session_notice`).

Every client the CLI builds (:mod:`beakr_cli._private_api.client`) sends the ids
confirmed so far (:func:`attach`):

- ``beakr --confirm-lock <id> ...`` (or ``BEAKR_CONFIRM_LOCK``), for scripts and
  agents: the id a held command named, sent on every request (:func:`declare`);
- at a terminal, the held request asks "Enter Partner A? ..." and, on yes, is
  sent once more with the id (:func:`entered_after`);
- inside ``beakr mcp``, a tool's ``confirm_lock`` argument, for that call only
  (:func:`take`, :func:`confirming`), as the hosted MCP server does. The server
  never asks: the held answer goes back to the agent, word for word.

Ids only: the API refuses anything else, so nothing is confirmed blindly.
"""

from __future__ import annotations

import contextlib
from collections.abc import Collection, Iterator
from contextvars import ContextVar
from typing import Any
from uuid import UUID

HEADER = "X-Beakr-Confirm-Lock"
CODE = "session_lock_confirm"
#: The MCP tools' argument (the shared surface's ``confirm_lock_property``).
ARGUMENT = "confirm_lock"
ENV = "BEAKR_CONFIRM_LOCK"

#: Ids this process confirms: ``--confirm-lock``, and each one a person entered here.
_DECLARED: list[str] = []
#: Ids one MCP tool call confirms (its ``confirm_lock`` argument).
_CALL: ContextVar[tuple[str, ...]] = ContextVar("beakr_confirm_lock_call", default=())
#: False inside ``beakr mcp``: the agent confirms, by repeating the call.
_ASKS = [True]


class InvalidConfirmLock(ValueError):
    """A ``confirm_lock`` value that is not project ids."""


def parse(raw: Any, *, label: str = ARGUMENT) -> tuple[str, ...]:
    """The project ids ``raw`` names (comma-separated), normalized and deduplicated.

    ``None`` and ``""`` confirm nothing. The message is the hosted server's for
    the MCP argument (``beakr.api.mcp_remote.server._take_confirm_lock``).
    """
    if raw is None or raw == "":
        return ()
    ids: list[str] = []
    for part in str(raw).split(","):
        part = part.strip()
        try:
            ids.append(str(UUID(part)))
        except ValueError:
            raise InvalidConfirmLock(
                f"{label} takes the id of the project to enter; {part[:60]!r} is not one."
            ) from None
    return tuple(dict.fromkeys(ids))


def declare(raw: str | None) -> None:
    """``--confirm-lock`` / ``BEAKR_CONFIRM_LOCK``: confirm these ids on every request."""
    for project in parse(raw, label="--confirm-lock"):
        if project not in _DECLARED:
            _DECLARED.append(project)


def serve_without_asking() -> None:
    """``beakr mcp``: a held call answers the agent; nothing confirms on its own."""
    _ASKS[0] = False
    _DECLARED.clear()


def confirmed() -> tuple[str, ...]:
    """The ids the next request confirms."""
    return tuple(dict.fromkeys((*_DECLARED, *_CALL.get())))


def _add_header(request: Any) -> None:
    ids = confirmed()
    if ids:
        request.headers[HEADER] = ",".join(ids)


async def _add_header_async(request: Any) -> None:
    _add_header(request)


def attach(client: Any, *, is_async: bool) -> None:
    """Send the confirmed ids on every request of ``client``'s own HTTP client."""
    http = getattr(client, "_http", None)
    hooks = getattr(http, "event_hooks", None)
    if not isinstance(hooks, dict):
        return
    hook = _add_header_async if is_async else _add_header
    requests = list(hooks.get("request") or [])
    if hook not in requests:
        http.event_hooks = {**hooks, "request": [*requests, hook]}


# --- beakr mcp: the tools' confirm_lock argument ---------------------------------------


def take(
    tool: str, arguments: dict[str, Any], tools: Collection[str]
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """The call without ``confirm_lock``, and the ids it confirms.

    As the hosted server takes it: only on ``tools`` (the surface's
    ``confirm_lock`` list; another tool's own schema refuses it), at the top level
    or, when absent there, inside an ``arguments`` envelope. Raises
    :class:`InvalidConfirmLock` for anything but project ids.
    """
    if tool not in tools:
        return arguments, ()
    arguments = dict(arguments)
    raw = arguments.pop(ARGUMENT, None)
    nested = arguments.get("arguments")
    if raw is None and isinstance(nested, dict) and ARGUMENT in nested:
        nested = dict(nested)
        raw = nested.pop(ARGUMENT)
        arguments["arguments"] = nested
    return arguments, parse(raw)


@contextlib.contextmanager
def confirming(ids: tuple[str, ...]) -> Iterator[None]:
    """Requests inside the block (one MCP tool call) confirm ``ids``."""
    token = _CALL.set(ids)
    try:
        yield
    finally:
        _CALL.reset(token)


# --- a CLI command: ask at a terminal ---------------------------------------------------


def _field(exc: Any, key: str) -> Any:
    """``key`` beside the refusal's code (the envelope, then ``detail``), as the API sets it."""
    holders = [getattr(exc, "details", None)]
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        holders += [body.get("error"), body.get("detail")]
    for holder in holders:
        if isinstance(holder, dict) and holder.get(key) is not None:
            return holder[key]
    return None


def held(exc: BaseException) -> bool:
    return getattr(exc, "code", None) == CODE


def held_project(exc: BaseException) -> tuple[str | None, str | None]:
    """``(id, name)`` of the project a held request names."""
    project = _field(exc, "project")
    project = project if isinstance(project, dict) else {}
    found = _field(exc, "confirm_lock") or project.get("id")
    return (str(found) if found else None), (str(project["name"]) if project.get("name") else None)


def prompt_text(exc: BaseException) -> str:
    """What the terminal asks before entering (the spec's words)."""
    project, name = held_project(exc)
    name = name or project or "this project"
    cost = str(_field(exc, "cost") or "")
    if cost.startswith("becomes read-only"):
        return f"Enter {name}? This session will become read-only: it will not be able to write."
    return f"Enter {name}? This session will only be able to write to {name}."


def entered_after(exc: BaseException) -> bool:
    """Whether the person entered the project a held request named: send it again.

    Only at a terminal, outside ``beakr mcp``, and once per project. Declining
    ends the command (exit 1): nothing was read or written.
    """
    if not held(exc) or not _ASKS[0]:
        return False
    project, name = held_project(exc)
    if not project or project in confirmed():
        return False
    from beakr_cli import output
    from beakr_cli.prompts import confirm_if_interactive

    if not output.is_interactive():
        return False
    if not confirm_if_interactive(prompt_text(exc), default=False):
        import typer

        output.err_console.print(
            f"Not entering {name or project}: nothing was read or written.", markup=False
        )
        raise typer.Exit(1)
    _DECLARED.append(project)
    return True


def cli_message(exc: BaseException, command: tuple[str, ...] = ()) -> str:
    """The held refusal in a terminal's terms: the API's warning, then the CLI's retry.

    The API ends its message with the MCP retry (``confirm_lock="<id>"``); a
    command is repeated with ``beakr --confirm-lock <id>`` instead.
    """
    message = str(getattr(exc, "message", None) or exc)
    warning = message.split(" To go in, ", 1)[0].rstrip()
    project, name = held_project(exc)
    words = " ".join(command) if command else "<command>"
    retry = f"`beakr --confirm-lock {project} {words} ...`" if project else "`beakr --confirm-lock`"
    return (
        f"{warning} Nothing was read or written. To go in, run it again as {retry}; or ask "
        f"the user, or start a new session for {name or 'that project'}."
    )
