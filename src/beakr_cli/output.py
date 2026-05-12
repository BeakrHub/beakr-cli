"""Output formatting for CLI results."""

from __future__ import annotations

import json
import sys
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table

console = Console()
err_console = Console(stderr=True)


def is_piped() -> bool:
    """True if stdout is not a terminal (piped to another command)."""
    return not sys.stdout.isatty()


def print_json(data: Any) -> None:
    console.print(json.dumps(data, indent=2, default=str))


def print_markdown(text: str) -> None:
    console.print(Markdown(text))


def print_table(columns: list[str], rows: list[list[str]], title: str = "") -> None:
    table = Table(title=title, show_lines=False, pad_edge=False)
    for col in columns:
        table.add_column(col)
    for row in rows:
        table.add_row(*row)
    console.print(table)


def print_pages_table(pages: list[dict]) -> None:
    if is_piped():
        print_json(pages)
        return
    if not pages:
        err_console.print("No pages found.")
        return

    columns = ["Title", "Type", "Rev", "Updated"]
    rows = []
    for p in pages:
        rows.append([
            p.get("title", ""),
            p.get("page_type", ""),
            str(p.get("revision", "")),
            p.get("updated_at", "")[:10] if p.get("updated_at") else "",
        ])
    print_table(columns, rows)


def print_search_results(results: list[dict]) -> None:
    if is_piped():
        print_json(results)
        return
    if not results:
        err_console.print("No results found.")
        return

    for r in results:
        title = r.get("title", "Untitled")
        score = r.get("score", 0)
        snippet = r.get("snippet", r.get("summary", ""))[:120]
        console.print(f"[bold]{title}[/bold]  score={score:.2f}")
        if snippet:
            console.print(f"  {snippet}")
        console.print()


def print_page(page: dict, *, content_only: bool = False) -> None:
    if is_piped():
        print_json(page)
        return
    if content_only:
        print_markdown(page.get("content", ""))
        return

    console.print(f"[bold]{page.get('title', 'Untitled')}[/bold]")
    console.print(f"Type: {page.get('page_type', '')}  Rev: {page.get('revision', '')}")
    console.print()
    print_markdown(page.get("content", ""))
