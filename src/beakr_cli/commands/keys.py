"""API key commands: ``beakr keys ls|create|revoke|scopes``.

``create`` opens the web app: the API refuses key creation from keys and CLI tokens.
"""

from __future__ import annotations

import base64
import json
import re
import webbrowser
from typing import Any

import typer

from beakr_cli.app_url import API_KEYS_PATH, app_url
from beakr_cli.output import console, emit, err_console, is_interactive, print_table, short
from beakr_cli.project_ref import project_ref_callback
from beakr_cli.sdk import get_sdk

app = typer.Typer(help="List and revoke API keys; create them in the web app.")

_json_opt = typer.Option(False, "--json", "-j", help="Output raw JSON.")
# Every key records the client it is for; the API refuses one without.
KEY_CLIENT = "beakr-cli"
CLIENT_NAME_MAX_LENGTH = 120
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _scopes_cell(key: dict[str, Any]) -> str:
    scopes = key.get("scopes")
    if scopes is None:
        return "legacy (full access)"
    return ", ".join(scopes) or "-"


@app.command("ls")
def list_keys(json_out: bool = _json_opt) -> None:
    """List your API keys."""
    with get_sdk() as sdk:
        keys = sdk.keys.list()

    def render(rows: list[dict[str, Any]]) -> None:
        if not rows:
            err_console.print("No API keys.")
            return
        print_table(
            ["ID", "Name", "Prefix", "Scopes", "Expires", "Last used"],
            [
                [
                    str(k.get("id", "")),
                    short(k.get("name"), 24),
                    str(k.get("key_prefix") or ""),
                    short(_scopes_cell(k), 40),
                    str(k.get("expires_at") or "never")[:10],
                    str(k.get("last_used_at") or "-")[:10],
                ]
                for k in rows
            ],
            title="API keys",
        )
        if any(k.get("legacy") or k.get("scopes") is None for k in rows if not k.get("revoked_at")):
            err_console.print(
                "[dim]Legacy keys have full access. Replace them with scoped keys "
                "(`beakr keys create`) and revoke the old ones.[/dim]"
            )

    emit(keys, json_out=json_out, render=render)


def _expiry_days(value: str | None) -> int | None:
    if value is None:
        return None
    text = value.strip().lower().removesuffix("d")
    if not text.isdigit() or not 1 <= int(text) <= 365:
        raise typer.BadParameter("--expires-in takes 1 to 365 days, for example 90 or 90d.")
    return int(text)


def _resolve_projects(sdk: Any, wanted: list[str]) -> list[str]:
    ids: list[str] = []
    by_name: dict[str, str] | None = None
    for item in wanted:
        if _UUID.fullmatch(item):
            ids.append(item.lower())
            continue
        if by_name is None:
            by_name = {
                str(p.get("name", "")).lower(): str(p["id"])
                for p in sdk.projects.list()
                if p.get("id")
            }
        match = by_name.get(item.lower())
        if match is None:
            raise typer.BadParameter(f"No project named {item!r}; pass its id instead.")
        ids.append(match)
    return list(dict.fromkeys(ids))


def _encode_request(request: dict[str, Any]) -> str:
    raw = json.dumps(request, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


@app.command("create")
def create_key(
    name: str | None = typer.Option(None, "--name", help="The key's name."),
    scope: list[str] = typer.Option(
        [], "--scope", "-s", help="A scope to grant; repeat for more (see `beakr keys scopes`)."
    ),
    project: list[str] = typer.Option(
        [],
        "--project",
        "-P",
        help="Confine the key to a project (id or name); repeatable.",
        callback=project_ref_callback,
    ),
    project_p: list[str] = typer.Option([], "-p", hidden=True, callback=project_ref_callback),
    expires_in: str | None = typer.Option(
        None, "--expires-in", help="Days until the key expires, 1 to 365 (default 90)."
    ),
    never_expires: bool = typer.Option(
        False, "--never-expires", help="Ask for a key that never expires (needs an org setting)."
    ),
    client: str = typer.Option(
        KEY_CLIENT,
        "--client",
        help="The client the key is for (claude-code, codex, a CI system); recorded on the key.",
    ),
    no_browser: bool = typer.Option(
        False, "--no-browser", help="Print the page's address without opening it."
    ),
    json_out: bool = _json_opt,
) -> None:
    """Create an API key: open the web page with what you asked for filled in.

    Keys are minted only from a signed-in browser session: the API refuses key
    creation from an API key or a CLI sign-in token, so a leaked token cannot
    mint itself a long-lived key. Pass `--scope`, `--project`, `--expires-in` or
    `--name` and the page opens with the form filled in; you review it and press
    Create there (with a recent second factor). Every key records the client it
    is for: `--client` (default `beakr-cli`) fills that in. `beakr keys scopes`
    lists what each scope allows.
    """
    # -p was this option's short flag before -P everywhere; still accepted.
    project = [*(project or []), *(project_p or [])]

    days = _expiry_days(expires_in)
    if never_expires and days is not None:
        raise typer.BadParameter("Use --expires-in or --never-expires, not both.")
    client_name = client.strip()
    if not client_name or len(client_name) > CLIENT_NAME_MAX_LENGTH:
        raise typer.BadParameter(
            f"--client must be 1 to {CLIENT_NAME_MAX_LENGTH} characters.", param_hint="--client"
        )
    scopes = list(dict.fromkeys(scope))
    project_ids: list[str] = []
    if scopes or project:
        with get_sdk() as sdk:
            if scopes:
                known = {str(s.get("name")) for s in sdk.keys.scopes()}
                unknown = [s for s in scopes if s not in known]
                if unknown:
                    raise typer.BadParameter(
                        f"Unknown scope(s): {', '.join(unknown)}. See `beakr keys scopes`."
                    )
            project_ids = _resolve_projects(sdk, project)
    # Always a request: the key's client is required, so the page is told
    # which one this is even when nothing else was asked for.
    request: dict[str, Any] = {
        "name": (name or "").strip(),
        "scopes": scopes,
        "project_ids": project_ids,
        "client_name": client_name,
    }
    if days is not None:
        request["expires_in_days"] = days
    if never_expires:
        request["never_expires"] = True

    web = app_url()
    url = f"{web.url}{API_KEYS_PATH}&newKey={_encode_request(request)}"
    opened = False
    if not no_browser and is_interactive():
        try:
            opened = webbrowser.open(url)
        except Exception:
            opened = False

    def render(data: dict[str, Any]) -> None:
        console.print("API keys are created in the Beakr web app, from a signed-in browser.")
        console.print("The form is filled in with your request; review it and press Create.")
        console.print(f"{'Opened' if data['opened'] else 'Open'}: {data['url']}", highlight=False)
        if web.guessed:
            err_console.print(
                f"The web app address {web.url} is a guess from the API URL; if the page "
                "does not load, set BEAKR_APP_URL to your Beakr web app's address.",
                highlight=False,
            )
        console.print(
            "[dim]Then use it with: beakr auth login --with-api-key (paste it), "
            "or BEAKR_API_KEY.[/dim]"
        )

    result: dict[str, Any] = {
        "url": url,
        "opened": opened,
        "request": request,
        "app_url_guessed": web.guessed,
    }
    emit(result, json_out=json_out, render=render)


@app.command("revoke")
def revoke_key(
    key_id: str = typer.Argument(..., help="Key id (from beakr keys ls)."),
    json_out: bool = _json_opt,
) -> None:
    """Revoke an API key immediately."""
    with get_sdk() as sdk:
        sdk.keys.revoke(key_id)
    emit(
        {"id": key_id, "revoked": True},
        json_out=json_out,
        render=lambda _: console.print(f"Revoked key {key_id}."),
    )


@app.command("scopes")
def list_scopes(json_out: bool = _json_opt) -> None:
    """List the scopes a key can be granted."""
    with get_sdk() as sdk:
        scopes = sdk.keys.scopes()

    def render(rows: list[dict[str, Any]]) -> None:
        print_table(
            ["Scope", "Description"],
            [[str(s.get("name", "")), str(s.get("description", ""))] for s in rows],
        )

    def lines(rows: list[dict[str, Any]]) -> list[str]:
        # name<TAB>description: the generic summary had no id column for a scope
        # and printed "-" where its description belongs.
        return [f"{s.get('name', '')}\t{s.get('description', '')}" for s in rows]

    emit(scopes, json_out=json_out, render=render, lines=lines)


def register(root: typer.Typer) -> None:
    root.add_typer(app, name="keys")
