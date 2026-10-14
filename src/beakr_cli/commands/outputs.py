"""``beakr outputs ls|show|export|history``: documents, sheets and decks Beakr made."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import typer

from beakr_cli._private_api.resources.outputs import EXPORT_FORMATS
from beakr_cli.output import (
    console,
    emit,
    err_console,
    page_rows,
    print_markdown,
    print_table,
    short,
)
from beakr_cli.project_ref import project_ref_callback
from beakr_cli.sdk import get_sdk

app = typer.Typer(help="Outputs: documents, sheets and decks agents and workflows produced.")

_json_opt = typer.Option(False, "--json", "-j", help="Output raw JSON.")


@app.command("ls")
def list_outputs(
    project: str | None = typer.Option(
        None, "--project", "-P", "--scope", help="Only this project.", callback=project_ref_callback
    ),
    search: str | None = typer.Option(None, "--search", "-q", help="Filter by title."),
    limit: int = typer.Option(50, "--limit", "-n", min=1, max=200),
    json_out: bool = _json_opt,
) -> None:
    """Outputs produced by agent runs and workflows, newest first."""
    with get_sdk() as sdk:
        page = sdk.outputs.list(project_id=project, search=search, limit=limit)

    def render(data: dict[str, Any]) -> None:
        items = data.get("data") or []
        if not items:
            err_console.print("No outputs.")
            return
        print_table(
            ["Output ID", "Title", "Project", "Created"],
            [
                [
                    str(i.get("id")),
                    short(i.get("title"), 44),
                    short(i.get("home_project_name"), 24),
                    str(i.get("created_at") or "")[:10],
                ]
                for i in items
            ],
            title=f"Outputs ({len(items)})" + (" (more: --limit)" if data.get("has_more") else ""),
        )

    emit(page, json_out=json_out, render=render)


def _source_version(source: dict[str, Any]) -> str:
    """The version a source was read at, marked when Beakr inferred it.

    ``version_basis`` ``current_at_generation`` means the run did not record the
    version it read; Beakr filled in the one current when the output was
    written, which must never read as the exact version (SPEC-338).
    """
    label = "version not recorded"
    if source.get("page_revision") is not None:
        label = f"page revision {source['page_revision']}"
    else:
        for key, name in (("artifact_version_id", "file version"), ("unit_version_id", "version")):
            if source.get(key):
                label = f"{name} {str(source[key])[:8]}"
                break
    if source.get("version_basis") == "current_at_generation":
        label += " (current at generation)"
    return label


def _print_as_of(meta: dict[str, Any], *, list_sources: bool) -> None:
    """When an agent last wrote the output, and what it had read (SPEC-338)."""
    generated_at = meta.get("generated_at")
    if not generated_at:
        err_console.print("[dim]No agent generation recorded for this output.[/dim]")
        return
    read = meta.get("sources_read") or []
    total = meta.get("sources_read_total") or len(read)
    cited = sum(1 for source in read if source.get("cited"))
    console.print(
        f"[dim]As of {generated_at}: the agent had read {total} source(s), {cited} cited.[/dim]",
        highlight=False,
    )
    if not list_sources:
        return
    for source in read:
        mark = "cited" if source.get("cited") else "read"
        console.print(
            f"  ({mark}) {short(source.get('name'), 60)}  [dim]{_source_version(source)}[/dim]",
            highlight=False,
        )
    if total > len(read):
        console.print(f"  [dim]... and {total - len(read)} more not listed.[/dim]")


@app.command("show")
def show_output(
    output_id: str = typer.Argument(..., help="Output id."),
    revision: int | None = typer.Option(None, "--revision", "-r", help="An earlier revision."),
    sources: bool = typer.Option(
        False,
        "--sources",
        "-s",
        help="Also list every source the agent had read, with the version it read.",
    ),
    json_out: bool = _json_opt,
) -> None:
    """Print an output's content (current, or --revision N) and when it was generated."""
    with get_sdk() as sdk:
        meta = sdk.outputs.get(output_id)
        content: Any = meta.get("content")
        if revision is not None:
            content = sdk.outputs.revision_content(output_id, revision)
    if json_out:
        emit({"output": meta, "content": content}, json_out=True)
        return
    shown = revision if revision is not None else meta.get("revision")
    console.print(
        f"[bold]{meta.get('title')}[/bold] ({meta.get('canvas_type') or 'doc'}, revision {shown})",
        highlight=False,
    )
    _print_as_of(meta, list_sources=sources)
    text = content.get("content") if isinstance(content, dict) else content
    print_markdown(str(text or ""))


@app.command("export")
def export_output(
    output_id: str = typer.Argument(..., help="Output id."),
    fmt: str = typer.Option("docx", "--format", "-F", help=f"One of {', '.join(EXPORT_FORMATS)}."),
    out: Path | None = typer.Option(None, "--out", "-o", help="File to write (default: title)."),
    no_sources: bool = typer.Option(False, "--no-sources", help="Leave out the sources appendix."),
    json_out: bool = _json_opt,
) -> None:
    """Export an output as a file (docx, pdf, xlsx, pptx, md or html).

    ``md`` is the output's text with its citations numbered and sources listed.
    """
    if fmt not in EXPORT_FORMATS:
        raise typer.BadParameter(f"--format must be one of {', '.join(EXPORT_FORMATS)}")
    with get_sdk(timeout=300.0) as sdk:
        if out is None:
            title = str(sdk.outputs.get(output_id).get("title") or output_id)
            safe = "".join(ch if ch.isalnum() or ch in " -_." else "_" for ch in title).strip()
            out = Path(f"{safe or output_id}.{fmt}")
        path = sdk.outputs.export(output_id, fmt, out, sources="none" if no_sources else None)
    emit(
        {"id": output_id, "format": fmt, "path": str(path)},
        json_out=json_out,
        render=lambda d: console.print(f"Wrote {d['path']}"),
    )


@app.command("history")
def output_history(
    output_id: str = typer.Argument(..., help="Output id."),
    limit: int = typer.Option(20, "--limit", "-n", min=1, max=200),
    json_out: bool = _json_opt,
) -> None:
    """An output's revisions: who changed it and when."""
    with get_sdk() as sdk:
        revisions = sdk.outputs.revisions(output_id, limit=limit)

    def render(data: Any) -> None:
        rows = page_rows(data)
        if not rows:
            err_console.print("No revisions.")
            return
        print_table(
            ["Revision", "When", "By", "Summary"],
            [
                [
                    str(r.get("revision_no") or r.get("revision")),
                    str(r.get("created_at") or "")[:16],
                    short(r.get("actor_name") or r.get("actor_type"), 20),
                    short(r.get("summary"), 50),
                ]
                for r in rows
            ],
            title=f"Revisions of {output_id}",
        )

    emit(revisions, json_out=json_out, render=render)


def register(root: typer.Typer) -> None:
    root.add_typer(app, name="outputs")
