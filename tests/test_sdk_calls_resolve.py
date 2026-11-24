"""Every SDK method the CLI calls exists in the beakr-sdk it pins, and takes the keywords sent.

The CLI pins a released beakr-sdk (``pyproject.toml``), but its commands were
written against beakr-python's main branch: ``beakr kb types ls`` called
``sdk.knowledge.list_card_types``, which no released SDK had, and raised
``AttributeError`` for every user (ENG-2524), as did the other card type
commands, the MCP tools over them, and ``projects.create(card_types=...)``.
The respx tests caught it only as a red CI run on main, after merging.

This reads every ``sdk.<area>[.<sub>].<method>(...)`` call in ``src/beakr_cli``
(``sdk`` is the name every call site gives the client ``get_sdk`` /
``call_api`` hands it) and resolves it on the clients built from the installed
SDK: the method must exist and take each keyword passed by name. A route the
pinned SDK has no method for goes through ``sdk.request`` (``card_type_routes``,
``cancels``, ``connected_sources``) until a release carries the method.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from beakr_cli._private_api import AsyncPrivateBeakr, PrivateBeakr

_SRC = Path(__file__).resolve().parent.parent / "src/beakr_cli"
_GENERATED = _SRC / "_private_api/_generated"


def _chain(node: ast.expr) -> list[str] | None:
    """``["sdk", "knowledge", "list_card_types"]`` for ``sdk.knowledge.list_card_types``."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        return [node.id, *reversed(parts)]
    return None


def _sdk_calls() -> list[tuple[str, list[str], ast.Call]]:
    """``(file:line, chain, call)`` for every call on a resource of ``sdk``."""
    calls = []
    for path in sorted(_SRC.rglob("*.py")):
        if _GENERATED in path.parents:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Call):
                continue
            chain = _chain(node.func)
            # sdk.<area>.<method>(...) and deeper; sdk.request(...) is the SDK's own.
            if chain and chain[0] == "sdk" and len(chain) >= 3:
                calls.append((f"{path.relative_to(_SRC)}:{node.lineno}", chain, node))
    return calls


def _resolve(client: object, chain: list[str]) -> object | None:
    target: object = client
    for name in chain[1:]:
        if not hasattr(target, name):
            return None
        target = getattr(target, name)
    return target


def _refused_keywords(method: object, call: ast.Call) -> list[str]:
    """The keywords passed by name that the method does not take."""
    try:
        parameters = inspect.signature(method).parameters  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return []
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
        return []
    # A ``**mapping`` splat cannot be read statically; the command tests cover it.
    return [kw.arg for kw in call.keywords if kw.arg is not None and kw.arg not in parameters]


def test_the_scan_finds_the_cli_s_sdk_calls() -> None:
    # Guards the scan itself: a refactor that renamed the client would make it vacuous.
    assert len(_sdk_calls()) > 200


def test_every_sdk_method_the_cli_calls_exists_in_the_pinned_sdk_with_those_keywords() -> None:
    clients = (
        PrivateBeakr(api_key="bk_live_test", base_url="https://api.test"),
        AsyncPrivateBeakr(api_key="bk_live_test", base_url="https://api.test"),
    )
    problems = []
    for where, chain, call in _sdk_calls():
        name = ".".join(chain)
        # A call site runs on one client or the other; resolving on either is enough.
        methods = [m for m in (_resolve(client, chain) for client in clients) if m is not None]
        if not methods:
            problems.append(f"{where}: {name} does not exist in the pinned beakr-sdk")
            continue
        refused = min((_refused_keywords(m, call) for m in methods), key=len)
        if refused:
            problems.append(f"{where}: {name} takes no {', '.join(refused)}")
    assert not problems, (
        "Calls the pinned beakr-sdk cannot serve; send them through sdk.request until a "
        "release has the method:\n" + "\n".join(problems)
    )
