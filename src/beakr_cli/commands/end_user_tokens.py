"""``beakr end-user-tokens mint|ls|revoke``: short-lived tokens for your product's users.

Embedded mode: sign in as your backend's service account (``BEAKR_API_KEY``)
and mint, for one of your users, a token limited to the projects (or the
external user) and scopes it names. It expires in 15 minutes by default (an
hour at most) and has no refresh token.
"""

from __future__ import annotations

from typing import Any

import typer

from beakr_cli.output import console, emit, err_console, print_table, short
from beakr_cli.project_ref import project_ref_callback
from beakr_cli.sdk import get_sdk

app = typer.Typer(help="Mint, list and revoke short-lived tokens for your product's users.")

_json_opt = typer.Option(False, "--json", "-j", help="Output raw JSON.")


@app.command("mint")
def mint(
    scope: list[str] = typer.Option(  # noqa: B008
        ...,
        "--scope",
        "-s",
        help="A scope the token carries; repeatable (see `beakr keys scopes`).",
    ),
    subject: str | None = typer.Option(
        None, "--subject", help="Your own id for the user (needs --project)."
    ),
    project: list[str] = typer.Option(  # noqa: B008
        [],
        "--project",
        "-P",
        help="A project the token may reach; repeatable.",
        callback=project_ref_callback,
    ),
    external_user: str | None = typer.Option(
        None,
        "--external-user",
        help="Mint for this external user, by its user_id (from `beakr external-users ls`), "
        "instead of --subject.",
    ),
    ttl: int | None = typer.Option(
        None, "--ttl", help="Seconds until it expires (default 900, at most 3600)."
    ),
    json_out: bool = _json_opt,
) -> None:
    """Mint a token for one of your users; prints the token (shown once)."""
    if bool(subject) == bool(external_user):
        raise typer.BadParameter(
            "give --subject with --project, or --external-user", param_hint="--subject"
        )
    if subject and not project:
        raise typer.BadParameter("--subject needs at least one --project", param_hint="--project")
    if external_user:
        from beakr_cli.commands.external_users import user_id_argument

        external_user = user_id_argument(external_user)
    with get_sdk() as sdk:
        minted = sdk.end_user_tokens.mint(
            scopes=scope,
            subject=subject,
            project_ids=project or None,
            external_user_id=external_user,
            ttl_seconds=ttl,
        )

    def render(data: dict[str, Any]) -> None:
        console.print(str(data.get("token") or ""), highlight=False, markup=False)
        err_console.print(
            f"Token {data.get('token_id')} expires {data.get('expires_at')}; it is shown only "
            "once. Revoke it with `beakr end-user-tokens revoke`.",
            highlight=False,
        )

    emit(minted, json_out=json_out, render=render)


@app.command("ls")
def list_tokens(
    subject: str | None = typer.Option(None, "--subject", help="Only this user's tokens."),
    json_out: bool = _json_opt,
) -> None:
    """The live end-user tokens you minted, newest first."""
    with get_sdk() as sdk:
        rows = sdk.end_user_tokens.list(subject=subject)

    def render(data: list[dict[str, Any]]) -> None:
        if not data:
            err_console.print("No live end-user tokens.")
            return
        print_table(
            ["ID", "Subject", "Scopes", "Expires"],
            [
                [
                    str(r.get("token_id") or r.get("id") or ""),
                    short(r.get("subject"), 30),
                    short(", ".join(r.get("scopes") or []), 40),
                    str(r.get("expires_at") or "")[:16],
                ]
                for r in data
            ],
            title=f"End-user tokens ({len(data)})",
        )

    emit(rows, json_out=json_out, render=render)


@app.command("revoke")
def revoke(
    token_id: str = typer.Argument(..., help="The token id from `mint` or `ls`."),
    json_out: bool = _json_opt,
) -> None:
    """Revoke one token; it stops working immediately."""
    with get_sdk() as sdk:
        result = sdk.end_user_tokens.revoke(token_id)

    def render(_data: Any) -> None:
        err_console.print(f"Revoked end-user token {token_id}.")

    emit(result, json_out=json_out, render=render, json_when_piped=False)


def register(root: typer.Typer) -> None:
    root.add_typer(app, name="end-user-tokens")
