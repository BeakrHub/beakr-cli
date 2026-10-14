"""``beakr memory ls|rm``: the lessons, preferences and facts agents carry for you."""

from __future__ import annotations

from typing import Any

import typer

from beakr_cli.output import emit, err_console, print_table, short
from beakr_cli.prompts import confirm_or_require_yes
from beakr_cli.sdk import get_sdk

app = typer.Typer(help="Your personal memory: what Beakr's agents remember about how you work.")

_json_opt = typer.Option(False, "--json", "-j", help="Output raw JSON.")


@app.command("ls")
def list_memories(
    kind: str | None = typer.Option(None, "--kind", help="lesson, preference or fact."),
    applies_to: str | None = typer.Option(None, "--applies-to", help="ask, workflow, wiki or all."),
    json_out: bool = _json_opt,
) -> None:
    """Your memories, pinned first."""
    with get_sdk() as sdk:
        memories = sdk.memories.list(kind=kind, applies_to=applies_to)

    def render(rows: list[dict[str, Any]]) -> None:
        if not rows:
            err_console.print("No memories.")
            return
        ordered = sorted(rows, key=lambda m: not m.get("pinned"))
        print_table(
            ["Memory ID", "Kind", "Applies to", "Memory", "Pinned"],
            [
                [
                    str(m.get("id")),
                    str(m.get("kind") or ""),
                    str(m.get("applies_to") or ""),
                    short(m.get("content"), 60),
                    "yes" if m.get("pinned") else "",
                ]
                for m in ordered
            ],
            title=f"Memories ({len(rows)})",
        )

    emit(memories, json_out=json_out, render=render)


@app.command("rm")
def remove_memory(
    memory_id: str = typer.Argument(..., help="Memory id from `beakr memory ls`."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    json_out: bool = _json_opt,
) -> None:
    """Delete a memory so agents stop using it."""
    confirm_or_require_yes(f"Delete memory {memory_id}?", yes=yes)
    with get_sdk() as sdk:
        sdk.memories.delete(memory_id)
    emit(
        {"id": memory_id, "deleted": True},
        json_out=json_out,
        render=lambda _: err_console.print(f"Deleted memory {memory_id}."),
        json_when_piped=False,
    )


def register(root: typer.Typer) -> None:
    root.add_typer(app, name="memory")
