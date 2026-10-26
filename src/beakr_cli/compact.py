"""Compact output: what a command prints when its stdout is not a terminal.

An agent (Claude Code, Codex) or a script reads the CLI through a pipe. Full
JSON objects there cost tokens on fields nobody reads (a project is 20+
fields), so a non-TTY run prints one line per object instead::

    e56b7c9b-4616-4c89-94bc-6c1c49af35cc	Platform Docs	slug=platform-docs	type=standard
    next: beakr kb ls -P platform-docs

Columns are tab-separated: the object's id, its name or title, then
``key=value`` for the few fields that tell objects apart. A ``next:`` line
names the command to run next when there is an obvious one (the next page of
a list, waiting for a job). ``--json`` (or ``BEAKR_OUTPUT=json``) prints the
full objects; ``BEAKR_OUTPUT=human`` forces the terminal rendering.
"""

from __future__ import annotations

import shlex
import sys
from collections.abc import Callable, Iterable
from typing import Any

#: Keys that identify an object, most specific first.
ID_KEYS = (
    "id",
    "run_id",
    "job_id",
    "thread_id",
    "proposal_id",
    "user_id",
    # A project membership (``projects members ls``) names its member this way.
    "principal_id",
    "key_id",
    # An end-user token (``end-user-tokens ls``): revoke takes this id.
    "token_id",
    "label_id",
    "project_id",
    "artifact_id",
    "page_id",
    "org_id",
    "client_id",
)

#: Keys that name an object for a reader.
LABEL_KEYS = (
    "name",
    "title",
    # An integration's own label (``integrations ls``).
    "label",
    "display_name",
    # Whom an end-user token is for; before client_name, which every token
    # minted by one backend shares.
    "subject",
    "client_name",
    "email",
    "external_id",
    "path",
    "filename",
)

#: Fields worth a ``key=value`` column, in this order, under these short names.
FIELD_KEYS = (
    ("slug", "slug"),
    ("project_type", "type"),
    ("provider", "provider"),
    ("page_type", "type"),
    ("artifact_type", "type"),
    ("kind", "kind"),
    ("type", "type"),
    ("status", "status"),
    ("state", "state"),
    ("membership_state", "state"),
    ("active", "active"),
    ("approved", "approved"),
    ("role", "role"),
    ("email", "email"),
    ("external_id", "external_id"),
    ("revision", "rev"),
    ("member_count", "members"),
    ("job_id", "job"),
    ("thread_id", "thread"),
    ("latest_run_id", "run"),
    ("created", "created"),
    ("accepted", "accepted"),
    ("deleted", "deleted"),
    ("revoked", "revoked"),
    ("expires_at", "expires"),
)

#: Values a command shows once (a new key, a signing secret): always printed, whole.
SECRET_KEYS = frozenset(
    {
        "key",
        "api_key",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "client_secret",
        "signing_secret",
        "plaintext_key",
    }
)

#: Links that are bookkeeping rather than somewhere to go; other ``*url`` fields
#: (a sign-in or connect link, a file's web view) are always printed, whole.
_BOOKKEEPING_URLS = frozenset(
    {"status_url", "batch_status_url", "resource_url", "avatar_url", "logo_url", "icon_url"}
)

#: Longest value shown in a column; longer text is cut with "...".
_MAX_VALUE = 80

#: How many ``key=value`` columns one line carries at most.
_MAX_FIELDS = 5

#: Envelope keys that hold the rows of a list response.
_ROW_KEYS = ("data", "items", "results")


def cell(value: Any, width: int = _MAX_VALUE) -> str:
    """A value as one tab-free, newline-free column."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        if all(isinstance(v, (str, int, float)) for v in value) and value:
            value = ",".join(str(v) for v in value)
        else:
            return f"[{len(value)}]"
    text = " ".join(str(value).split())
    return text if len(text) <= width else text[: width - 3] + "..."


def _scalar(value: Any) -> bool:
    if isinstance(value, (str, int, float, bool)):
        return value != ""
    return isinstance(value, (list, tuple)) and bool(value) and len(value) <= 5


def _always_shown(key: str, value: Any) -> bool:
    """A one-time secret or a link to act on: never dropped from a summary."""
    if not isinstance(value, str) or not value:
        return False
    if key in SECRET_KEYS or key.endswith(("_secret", "_token")):
        return True
    return key.endswith("url") and key not in _BOOKKEEPING_URLS


def _error_text(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, dict):
        text = value.get("message") or value.get("code")
        return str(text) if text else None
    return None


def _every_field(
    item: dict[str, Any], prefix: str = "", rows: list[str] | None = None
) -> list[str]:
    """Every non-empty field as ``key=value``; a nested object's fields as ``key.field=value``.

    A list of objects has no one-column form: each of its objects is appended
    to ``rows`` as a line of its own, led by the field's name.
    """
    columns: list[str] = []
    for key, value in item.items():
        if value is None or value == "" or value == [] or value == {}:
            continue
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            columns.extend(_every_field(value, prefix=f"{name}.", rows=rows))
        elif isinstance(value, (list, tuple)) and not all(
            isinstance(v, (str, int, float, bool)) for v in value
        ):
            # Kept whole, but as lines: the list as JSON in one column was the
            # one thing on the line a reader had to parse.
            if rows is not None:
                rows.extend(f"{name}\t{line}" for v in value for line in _lines_of(v))
        elif _always_shown(key, value):
            columns.append(f"{name}={' '.join(value.split())}")
        else:
            columns.append(f"{name}={cell(value)}")
    return columns


def _lines_of(value: Any) -> list[str]:
    """One element of a list of objects: every field, as its parent shows them.

    Its own lists of objects follow it on further lines.
    """
    if not isinstance(value, dict):
        return [cell(value, width=10_000)]
    rows: list[str] = []
    columns = _every_field(value, rows=rows)
    return ["\t".join(columns) or "-", *rows]


def summary_line(item: dict[str, Any]) -> str:
    """One object as ``id<TAB>label<TAB>key=value...``.

    An object with neither an id nor a name (an accept result, a count) is its
    fields as ``key=value`` columns. It used to be printed as raw JSON, the one
    shape in compact output a reader had to parse instead of read. A list of
    objects among those fields follows on lines of its own, one per object,
    each led by the field's name and showing every field too::

        fresh=false\tcoverage=partial
        changed\tkind=page\tpage_id=5e7b9d13-...\ttitle=Decision: Helios Lead Selection
    """
    id_key = next((k for k in ID_KEYS if _scalar(item.get(k))), None)
    label_key = next((k for k in LABEL_KEYS if _scalar(item.get(k))), None)
    if id_key is None and label_key is None:
        rows: list[str] = []
        columns = _every_field(item, rows=rows)
        lines = (["\t".join(columns)] if columns else []) + rows
        return "\n".join(lines) or "-"
    columns = [cell(item[id_key]) if id_key else "-"]
    if label_key:
        columns.append(cell(item[label_key]))
    used = {id_key, label_key}
    named: set[str] = set()
    for key, name in FIELD_KEYS:
        if len(columns) - 2 >= _MAX_FIELDS:
            break
        if key in used or name in named or not _scalar(item.get(key)):
            continue
        if label_key and cell(item[key]) == cell(item[label_key]):
            continue  # the name column already says it
        columns.append(f"{name}={cell(item[key])}")
        named.add(name)
        used.add(key)
    error = _error_text(item.get("error"))
    if error:
        columns.append(f"error={cell(error, width=300)}")
    for key, value in item.items():
        if key not in used and _always_shown(key, value):
            columns.append(f"{key}={' '.join(value.split())}")
    return "\t".join(columns)


def rows_of(data: Any) -> list[Any] | None:
    """The rows of a list response (a bare list or a ``{data: [...]}`` envelope)."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in _ROW_KEYS:
            if isinstance(data.get(key), list):
                return data[key]
    return None


def compact_lines(data: Any) -> list[str]:
    """The generic compact rendering of any command result."""
    rows = rows_of(data)
    if rows is not None:
        lines = [
            summary_line(row) if isinstance(row, dict) else cell(row, width=10_000) for row in rows
        ]
        if isinstance(data, dict):
            # Scalars beside the rows of an envelope (``restricted``, ``total``)
            # describe the whole list; cursor bookkeeping becomes the next: hint.
            extra = [
                f"{key}={cell(value)}"
                for key, value in data.items()
                if key not in _ROW_KEYS
                and key not in ("next_cursor", "has_more")
                and isinstance(value, (str, int, float, bool))
            ]
            if extra:
                lines.append("\t".join(extra))
        return lines
    if isinstance(data, dict):
        return [summary_line(data)]
    if data is None:
        return []
    return [cell(data, width=10_000)]


def _current_invocation() -> list[str]:
    """``beakr`` plus the arguments this process was run with."""
    return ["beakr", *sys.argv[1:]]


def next_page_hint(data: Any) -> str | None:
    """``beakr <same command> --cursor C`` when the list has another page."""
    if not isinstance(data, dict) or not data.get("has_more") or not data.get("next_cursor"):
        return None
    if not _command_takes("cursor"):
        return None
    argv = _current_invocation()
    kept: list[str] = []
    skip = False
    for arg in argv:
        if skip:
            skip = False
            continue
        if arg == "--cursor":
            skip = True
            continue
        if arg.startswith("--cursor="):
            continue
        kept.append(arg)
    return shlex.join([*kept, "--cursor", str(data["next_cursor"])])


def _command_takes(param: str) -> bool:
    import click

    ctx = click.get_current_context(silent=True)
    if ctx is None:
        return False
    return any(p.name == param for p in ctx.command.params)


Hint = str | Callable[[Any], str | None] | None


def print_compact(
    data: Any,
    *,
    lines: Callable[[Any], Iterable[str]] | None = None,
    hint: Hint = None,
) -> None:
    """Write the compact rendering of ``data`` and its ``next:`` hint to stdout."""
    out = list((lines or compact_lines)(data))
    next_hint = hint(data) if callable(hint) else hint
    next_hint = next_hint or next_page_hint(data)
    if next_hint:
        out.append(f"next: {next_hint}")
    if out:
        sys.stdout.write("\n".join(out) + "\n")
    sys.stdout.flush()
