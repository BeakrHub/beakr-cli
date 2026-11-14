"""Output formatting for CLI results."""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.table import Table
from rich.text import Text

from beakr_cli.terminal_text import visible


class _TerminalConsole(Console):
    """A console that never writes a character a terminal hides or obeys.

    What the CLI prints holds text other people wrote (a page title, an
    excerpt, a file name). Rich passes an escape sequence, a bidirectional
    override or a zero-width character through to the terminal, where it can
    erase or restyle the line it sits on; every string printed here shows them
    as their escapes instead (:func:`beakr_cli.terminal_text.visible`). Rich's
    own styling is added after this and is untouched; ``--json`` output is
    written by :func:`print_json`, not here.
    """

    def render_str(self, text: str, **options: Any) -> Text:
        return super().render_str(visible(text), **options)


console = _TerminalConsole()
err_console = _TerminalConsole(stderr=True)


def is_piped() -> bool:
    """True if stdout is not a terminal (piped to another command)."""
    return not sys.stdout.isatty()


def print_json(data: Any) -> None:
    # Write directly to stdout — Rich would terminal-wrap long string values,
    # injecting raw newlines that break `jq` and `json.loads`.
    sys.stdout.write(json.dumps(data, indent=2, default=str, ensure_ascii=False))
    sys.stdout.write("\n")


def print_markdown(text: str) -> None:
    console.print(Markdown(visible(text)))


def print_table(
    columns: list[str],
    rows: list[list[str]],
    title: str = "",
    *,
    whole: tuple[str, ...] = (),
) -> None:
    """``whole`` names columns never shortened to fit (ids a reader copies)."""
    # A title can carry server data too (a page title, a project name).
    table = Table(title=escape(title), show_lines=False, pad_edge=False)
    for index, col in enumerate(columns):
        if col in whole:
            widest = max([len(col), *(len(str(row[index])) for row in rows)])
            table.add_column(col, no_wrap=True, min_width=widest)
        else:
            table.add_column(col)
    for row in rows:
        # Cells carry server data (page titles, summaries, the agent name a
        # caller declared in X-Beakr-Agent): escape it so Rich prints it as text
        # instead of reading "[...]" as markup that could restyle or hide it.
        table.add_row(*(escape(str(cell)) for cell in row))
    console.print(table)


def print_pages_table(pages: list[dict]) -> None:
    if machine_output(pages, json_out=False):
        return
    if not pages:
        err_console.print("No pages found.")
        return

    columns = ["Title", "Type", "Rev", "Updated"]
    rows = []
    for p in pages:
        rows.append(
            [
                p.get("title", ""),
                p.get("page_type", ""),
                str(p.get("revision", "")),
                p.get("updated_at", "")[:10] if p.get("updated_at") else "",
            ]
        )
    print_table(columns, rows)


_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_CITATION_TOKEN = re.compile(r"\{\{[^{}]*\}\}")
_WIKI_LINK = re.compile(r"\[\[([^\[\]|]+)(?:\|([^\[\]]+))?\]\]")


def readable_snippet(text: str) -> str:
    """A stored-markdown excerpt as prose: no section markers or citation tokens.

    A search hit's snippet is cut from the page's stored markdown, which carries
    ``<!-- sec:... -->`` markers, ``{{artifact:...}}`` citation tokens and
    ``[[Page]]`` links. Shown raw they read as noise; the cited documents are one
    ``kb cat`` away, where the door renders them as readable references.
    """
    text = _HTML_COMMENT.sub(" ", text)
    text = _CITATION_TOKEN.sub("", text)
    text = _WIKI_LINK.sub(lambda m: m.group(2) or m.group(1), text)
    return " ".join(text.split())


def search_notes(page: Any, query: str) -> list[str]:
    """What a person or agent must be told alongside a page of search results.

    An empty answer says weaker matches may exist and names ``kb sections``,
    which ranks every section by meaning; a meaning-ranked answer whose best
    match is weak carries the engine's own WEAK warning (``note``, the wording
    ``kb sections`` uses). Nothing for a good answer.
    """
    import shlex

    if not page_rows(page):
        return [
            "No strong matches. Weaker matches may exist: try "
            f"`beakr kb sections {shlex.quote(query)}`."
        ]
    note = page.get("note") if isinstance(page, dict) else None
    return [str(note)] if note else []


def print_search_results(results: list[dict], *, notes: list[str] | None = None) -> None:
    if machine_output(results, json_out=False):
        return
    for note in notes or []:
        err_console.print(note, markup=False, highlight=False)
    if not results:
        return

    for r in results:
        title = r.get("title", "Untitled")
        score = r.get("score") or 0
        # content_snippet is the part of the page that matched (WikiSearchHit).
        snippet = readable_snippet(
            str(r.get("content_snippet") or r.get("snippet") or r.get("summary") or "")
        )[:300]
        band = f"  relevance={r['relevance']}" if r.get("relevance") else ""
        console.print(
            f"[bold]{escape(str(title))}[/bold]  score={score:.2f}{escape(band)}", highlight=False
        )
        if r.get("breadcrumb"):
            console.print(f"  [dim]{escape(str(r['breadcrumb']))}[/dim]", highlight=False)
        if snippet:
            console.print(f"  {snippet}", markup=False, highlight=False)
        console.print()


def is_interactive() -> bool:
    """True when a person can answer a prompt: never prompt when stdin is piped."""
    return sys.stdin.isatty()


def output_mode(json_out: bool = False) -> str:
    """How a command prints its result: ``json``, ``compact`` or ``human``.

    ``--json`` (or ``BEAKR_OUTPUT=json``) prints full objects. Otherwise a
    terminal gets the human rendering and anything else -- an agent or a
    script reading a pipe -- the compact one-line-per-object summary
    (:mod:`beakr_cli.compact`). ``BEAKR_OUTPUT=compact|human`` forces either.
    """
    forced = os.environ.get("BEAKR_OUTPUT", "").strip().lower()
    if json_out or forced == "json":
        return "json"
    if forced in ("compact", "human"):
        return forced
    return "compact" if is_piped() else "human"


def machine_output(
    data: Any,
    *,
    json_out: bool,
    lines: Any = None,
    hint: Any = None,
) -> bool:
    """Print ``data`` as JSON or compact lines unless a person is reading; True if printed.

    For commands that render their human output inline::

        if machine_output(rows, json_out=json_out):
            return
    """
    mode = output_mode(json_out)
    if mode == "json":
        print_json(data)
        return True
    if mode == "compact":
        from beakr_cli.compact import print_compact

        print_compact(data, lines=lines, hint=hint)
        return True
    return False


def emit(
    data: Any,
    *,
    json_out: bool,
    render: Any = None,
    json_when_piped: bool = True,
    lines: Any = None,
    hint: Any = None,
) -> None:
    """Print ``data`` as JSON (``--json``), compact lines (not a terminal) or ``render(data)``.

    ``lines(data)`` overrides the generic compact rendering and ``hint`` (a
    string, or a function of ``data``) is printed as its ``next:`` line.
    ``json_when_piped=False`` is for commands whose human output is a status
    line on stderr (``rm``, ``revoke``): piped, they keep printing that line
    (with any next-step hint) and leave stdout empty; ``--json`` still prints JSON.
    """
    mode = output_mode(json_out)
    if mode == "json" or (render is None and mode == "human"):
        print_json(data)
        return
    if mode == "compact" and (json_when_piped or render is None):
        from beakr_cli.compact import print_compact

        print_compact(data, lines=lines, hint=hint)
        return
    render(data)


@contextmanager
def human_output_to_stderr(enabled: bool) -> Iterator[None]:
    """While ``enabled``, send ``console`` lines to stderr so stdout stays one JSON document.

    For commands whose progress is printed by many helpers (setup, install,
    sign-in): with ``--json`` the person still sees the progress, and a script
    reading stdout gets only the result ``emit`` prints at the end.
    """
    if not enabled:
        yield
        return
    previous = console.stderr
    console.stderr = True
    try:
        yield
    finally:
        console.stderr = previous


def page_rows(page: Any) -> list[dict[str, Any]]:
    """The rows of one page of a public list: ``{data, next_cursor, has_more}``.

    Every public list answers that envelope (C064: renderers that read the
    retired ``threads``/``revisions``/``results``/``events`` keys printed "none"
    for non-empty pages). A whole list an SDK helper already collected is
    returned as it is.
    """
    if isinstance(page, list):
        return page
    if isinstance(page, dict):
        rows = page.get("data")
        return list(rows) if isinstance(rows, list) else []
    return []


def short(value: Any, width: int = 40) -> str:
    """A table cell: stringified, single-line, and truncated."""
    text = "" if value is None else " ".join(str(value).split())
    return text if len(text) <= width else text[: width - 3] + "..."
