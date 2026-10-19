"""``beakr integrations ls|connect|sync|status|coverage|scope|rm|disconnect|tools|call``.

Connecting an account finishes in a browser (the provider's OAuth consent), so
``connect`` prints the link and opens it when run in a terminal. The rest works
from any sign-in with the ``integrations:read`` / ``integrations:admin`` scopes.
``tools`` lists the read-only tools for what you have connected and ``call``
runs one, the way Beakr's own agent queries them.
"""

from __future__ import annotations

import json
import webbrowser
from typing import Any

import typer

from beakr_cli.output import console, emit, err_console, is_interactive, print_table, short
from beakr_cli.project_ref import project_ref_callback
from beakr_cli.prompts import confirm_or_require_yes
from beakr_cli.sdk import get_sdk

app = typer.Typer(help="Connect, sync and check coverage of integrations.")

_json_opt = typer.Option(False, "--json", "-j", help="Output raw JSON.")


@app.command("ls")
def list_integrations(json_out: bool = _json_opt) -> None:
    """List connected integrations and the projects each one feeds."""
    with get_sdk() as sdk:
        listing = sdk.integrations.list()

    def render(data: dict[str, Any]) -> None:
        rows = data.get("data") or []
        if not rows:
            err_console.print("No integrations yet. Connect one with `beakr integrations connect`.")
            return
        print_table(
            ["ID", "Provider", "Label", "Scope", "Health", "Projects"],
            [
                [
                    str(r.get("id") or ""),
                    str(r.get("provider") or ""),
                    short(r.get("label"), 24),
                    str(r.get("scope") or ""),
                    str(r.get("health_status") or ""),
                    str(len(r.get("projects") or [])),
                ]
                for r in rows
            ],
            title=f"Integrations ({len(rows)})",
        )

    emit(listing, json_out=json_out, render=render)


@app.command("connect")
def connect(
    provider: str = typer.Argument(..., help="e.g. google-drive, slack, notion, github, jira."),
    org: bool = typer.Option(False, "--org", help="Connect for the whole organization (admins)."),
    no_browser: bool = typer.Option(False, "--no-browser", help="Print the link only."),
    json_out: bool = _json_opt,
) -> None:
    """Get a browser link that connects PROVIDER; OAuth completes in the browser."""
    with get_sdk() as sdk:
        link = sdk.integrations.connect(provider, scope="org" if org else "user")
    opened = False
    if not no_browser and is_interactive():
        try:
            opened = webbrowser.open(link["connect_url"])
        except Exception:
            opened = False

    def render(data: dict[str, Any]) -> None:
        verb = "Opened" if opened else "Open"
        console.print(f"{verb}: {data.get('connect_url')}", highlight=False, markup=False)
        err_console.print(
            "Finish connecting in the browser; `beakr integrations ls` shows it once done."
        )

    emit({**link, "opened": opened}, json_out=json_out, render=render)


@app.command("status")
def status(
    connector_id: str = typer.Argument(..., help="Integration id from `beakr integrations ls`."),
    json_out: bool = _json_opt,
) -> None:
    """Health of an integration and each project source's latest sync."""
    with get_sdk() as sdk:
        result = sdk.integrations.status(connector_id)

    def render(data: dict[str, Any]) -> None:
        console.print(
            f"{data.get('provider')} {data.get('label') or ''}: health {data.get('health_status')}",
            highlight=False,
            markup=False,
        )
        if data.get("health_error"):
            err_console.print(f"Last error: {data['health_error']}", markup=False)
        print_table(
            ["Project", "Source", "Sync", "Files done", "Failed"],
            [
                [
                    str(s.get("project_id") or ""),
                    short(s.get("name"), 30),
                    str(s.get("status") or "never"),
                    f"{s.get('files_completed', 0)}/{s.get('files_total', 0)}",
                    str(s.get("files_failed", 0)),
                ]
                for s in data.get("syncs") or []
            ],
            title="Project sources",
        )

    emit(result, json_out=json_out, render=render)


@app.command("coverage")
def coverage(
    connector_id: str = typer.Argument(..., help="Integration id from `beakr integrations ls`."),
    json_out: bool = _json_opt,
) -> None:
    """What the integration's sources hold, what Knowledge read and compiled, and why not."""
    with get_sdk() as sdk:
        result = sdk.integrations.coverage(connector_id)

    def render(data: dict[str, Any]) -> None:
        console.print(
            f"Known {data.get('known', 0)}, indexed {data.get('indexed', 0)}, "
            f"compiled {data.get('compiled', 0)}, not readable {data.get('not_readable', 0)}, "
            f"removed {data.get('removed', 0)}.",
            highlight=False,
            markup=False,
        )
        rows = [
            [status, reason, str(count)]
            for status, reasons in sorted((data.get("not_compiled") or {}).items())
            for reason, count in sorted(reasons.items())
        ]
        if rows:
            print_table(["Status", "Reason", "Items"], rows, title="Not compiled")

    emit(result, json_out=json_out, render=render)


@app.command("sync")
def sync(
    connector_id: str = typer.Argument(..., help="Integration id from `beakr integrations ls`."),
    project: str | None = typer.Option(
        None, "--project", "-P", "--scope", help="Only this project.", callback=project_ref_callback
    ),
    json_out: bool = _json_opt,
) -> None:
    """Sync the integration's project sources now."""
    with get_sdk() as sdk:
        result = sdk.integrations.sync(connector_id, project_id=project)

    def render(data: dict[str, Any]) -> None:
        results = data.get("results") or []
        if not results:
            err_console.print("This integration feeds no project you can sync.")
            return
        print_table(
            ["Project", "Source", "Result", "Detail"],
            [
                [
                    str(r.get("project_id") or ""),
                    short(r.get("name"), 30),
                    str(r.get("status") or ""),
                    short(r.get("message"), 50),
                ]
                for r in results
            ],
            title="Sync",
        )

    emit(result, json_out=json_out, render=render)


@app.command("rm")
def disconnect(
    connector_id: str = typer.Argument(..., help="Integration id from `beakr integrations ls`."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    json_out: bool = _json_opt,
) -> None:
    """Disconnect an integration, including at the provider."""
    confirm_or_require_yes(f"Disconnect integration {connector_id}?", yes=yes)
    with get_sdk() as sdk:
        sdk.integrations.disconnect(connector_id)
    emit(
        {"id": connector_id, "disconnected": True},
        json_out=json_out,
        render=lambda _: err_console.print(f"Disconnected {connector_id}.", markup=False),
        json_when_piped=False,
    )


def _scope_item(value: str) -> dict[str, str]:
    """``TYPE:ID[:NAME]`` as the connector scope item the API takes."""
    kind, _, rest = value.partition(":")
    item_id, _, name = rest.partition(":")
    if not kind or not item_id:
        raise typer.BadParameter(f"Expected TYPE:ID[:NAME], got {value!r}", param_hint="--item")
    return {"type": kind, "id": item_id, "name": name or item_id}


@app.command("scope")
def scope(
    connector_id: str = typer.Argument(..., help="Integration id from `beakr integrations ls`."),
    all_items: bool = typer.Option(
        False, "--all", help="Let it read everything the account can see."
    ),
    item: list[str] = typer.Option(  # noqa: B008 - typer reads the default
        [],
        "--item",
        help="Limit it to TYPE:ID[:NAME], e.g. folder:1AbC:Contracts; repeatable.",
    ),
    json_out: bool = _json_opt,
) -> None:
    """Show what an integration may read, or limit it with --item / open it with --all."""
    if all_items and item:
        raise typer.BadParameter("Use --all or --item, not both.")
    with get_sdk() as sdk:
        if all_items:
            result = sdk.integrations.set_scope(connector_id, "all")
        elif item:
            result = sdk.integrations.set_scope(
                connector_id, "restricted", items=[_scope_item(value) for value in item]
            )
        else:
            result = sdk.integrations.get_scope(connector_id)

    def render(data: Any) -> None:
        data = data if isinstance(data, dict) else {}
        mode = data.get("mode") or ("all" if all_items else "restricted" if item else "unknown")
        if mode == "all":
            console.print(
                f"{connector_id} reads everything the connected account can see.", markup=False
            )
            return
        items = data.get("items") or []
        console.print(f"{connector_id} reads {len(items)} selected item(s):", markup=False)
        for entry in items:
            label = entry.get("name") or entry.get("title") or entry.get("id")
            console.print(f"  {entry.get('id')}  {label}", highlight=False, markup=False)

    emit(result, json_out=json_out, render=render)


@app.command("tools")
def tools(
    provider: str | None = typer.Option(None, "--provider", help="Only this provider's tools."),
    connector: str | None = typer.Option(
        None, "--connector", help="Only tools for this integration id."
    ),
    json_out: bool = _json_opt,
) -> None:
    """The read-only tools for the integrations you have connected."""
    with get_sdk() as sdk:
        found = sdk.integrations.tools(provider=provider, connector_id=connector)

    def render(rows: list[dict[str, Any]]) -> None:
        if not rows:
            err_console.print(
                "No tools: connect an integration first with `beakr integrations connect`."
            )
            return
        print_table(
            ["Tool", "Provider", "Connections", "Description"],
            [
                [
                    str(r.get("name") or ""),
                    str(r.get("provider") or ""),
                    str(len(r.get("connections") or [])),
                    short(r.get("description"), 60),
                ]
                for r in rows
            ],
            title=f"Integration tools ({len(rows)})",
        )

    emit(found, json_out=json_out, render=render)


def _tool_arguments(pairs: list[str], raw: str | None) -> dict[str, Any]:
    """``--args JSON`` as the base, then each ``--arg KEY=VALUE`` (a JSON value or a string)."""
    arguments: dict[str, Any] = {}
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise typer.BadParameter(f"not JSON: {exc}", param_hint="--args") from exc
        if not isinstance(parsed, dict):
            raise typer.BadParameter("give a JSON object", param_hint="--args")
        arguments.update(parsed)
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            raise typer.BadParameter(f"expected KEY=VALUE, got {pair!r}", param_hint="--arg")
        try:
            arguments[key] = json.loads(value)
        except json.JSONDecodeError:
            arguments[key] = value
    return arguments


@app.command("call")
def call(
    tool: str = typer.Argument(..., help="A tool name from `beakr integrations tools`."),
    arg: list[str] = typer.Option(  # noqa: B008
        [], "--arg", "-a", help="One argument as KEY=VALUE (VALUE may be JSON); repeatable."
    ),
    args_json: str | None = typer.Option(None, "--args", help="All arguments as one JSON object."),
    connector: str | None = typer.Option(
        None, "--connector", help="Which integration, when the provider is connected twice."
    ),
    json_out: bool = _json_opt,
) -> None:
    """Run one read-only integration tool and print what it found."""
    arguments = _tool_arguments(arg, args_json)
    with get_sdk() as sdk:
        result = sdk.integrations.call_tool(tool, arguments, connector_id=connector)

    def render(data: dict[str, Any]) -> None:
        content = data.get("content")
        text = content if isinstance(content, str) else json.dumps(content, indent=2)
        console.print(text, highlight=False, markup=False)
        sources = data.get("sources") or []
        if sources:
            err_console.print(
                "Sources: " + ", ".join(str(s.get("name") or "") for s in sources),
                highlight=False,
                markup=False,
            )

    emit(result, json_out=json_out, render=render)
    if isinstance(result, dict) and result.get("is_error"):
        raise typer.Exit(1)


def register(root: typer.Typer) -> None:
    root.add_typer(app, name="integrations")
    # `disconnect` is the spec's name for `rm`; both stay.
    app.command("disconnect")(disconnect)
