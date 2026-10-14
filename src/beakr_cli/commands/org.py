"""Organization admin commands: ``beakr org credentials|service-accounts|policy``.

All of these need an organization admin. Creating service accounts, minting
their keys and changing the policy also need a signed-in browser session (the
API refuses them to keys and CLI tokens with ``session_required``) and, when the
org requires step-up, a recently verified second factor (``mfa_required``);
the error message says which.

The audit log, OAuth client approval, SCIM and data retention commands are not
in this release.

The CLI never holds a browser session: ``beakr auth login`` yields a
programmatic token. Against Beakr's hosted API those commands are therefore
always refused, and the supported path is the web app, under Organization
settings > Developer: Service accounts (which shows a key once, to copy into a
CI secret or a service account file) and Authentication policy. They succeed only against a local
development server through the development-only identity ``beakr auth dev``
sets, which the hosted API never accepts.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

import typer

from beakr_cli.commands.keys import KEY_CLIENT
from beakr_cli.output import console, emit, err_console, print_table, short
from beakr_cli.project_ref import project_ref_callback
from beakr_cli.prompts import confirm_or_require_yes
from beakr_cli.sdk import get_sdk

app = typer.Typer(help="Organization admin: credentials, service accounts and sign-in policy.")
credentials_app = typer.Typer(help="Every API key and CLI/agent token in the organization.")
accounts_app = typer.Typer(
    help="Service accounts: non-human members that own scoped keys. Create them and "
    "mint their keys in the web app (Organization settings > Developer > Service "
    "accounts); a CLI sign-in or key is refused with session_required."
)
policy_app = typer.Typer(
    help="Personal keys, key lifetime, step-up MFA, allowed clients, single sign-on."
)

_json_opt = typer.Option(False, "--json", "-j", help="Output raw JSON.")
_org_opt = typer.Option(None, "--org", help="Organization id (default: the org you act in).")
_yes_opt = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt.")


class CredentialKind(str, Enum):
    api_key = "api_key"
    access_token = "access_token"


class AccountRole(str, Enum):
    member = "member"
    admin = "admin"
    viewer = "viewer"


# -- credentials -------------------------------------------------------------


@credentials_app.command("ls")
def list_credentials(org: str | None = _org_opt, json_out: bool = _json_opt) -> None:
    """List live keys and tokens (never their secrets)."""
    with get_sdk() as sdk:
        rows = sdk.org.credentials.list(org_id=org)

    def render(items: list[dict[str, Any]]) -> None:
        if not items:
            err_console.print("No live credentials.")
            return
        print_table(
            ["Kind", "ID", "User", "Name", "Scopes", "Last used", "Expires"],
            [
                [
                    str(c.get("kind") or ""),
                    str(c.get("id") or ""),
                    str(c.get("user_id") or ""),
                    short(c.get("name") or c.get("client_name"), 24),
                    short(
                        "legacy (full access)"
                        if c.get("kind") == "api_key" and c.get("scopes") is None
                        else ", ".join(c.get("scopes") or []) or "-",
                        36,
                    ),
                    str(c.get("last_used_at") or "-")[:10],
                    str(c.get("expires_at") or "never")[:10],
                ]
                for c in items
            ],
            title="Credentials",
        )

    emit(rows, json_out=json_out, render=render)


@credentials_app.command("revoke")
def revoke_credential(
    kind: CredentialKind = typer.Argument(..., help="api_key or access_token."),
    credential_id: str = typer.Argument(..., help="Id from `beakr org credentials ls`."),
    org: str | None = _org_opt,
    yes: bool = _yes_opt,
    json_out: bool = _json_opt,
) -> None:
    """Revoke a credential. An access token takes its whole sign-in with it."""
    confirm_or_require_yes(f"Revoke {kind.value} {credential_id}?", yes)
    with get_sdk() as sdk:
        sdk.org.credentials.revoke(kind.value, credential_id, org_id=org)
    emit(
        {"kind": kind.value, "id": credential_id, "revoked": True},
        json_out=json_out,
        render=lambda _: console.print(f"Revoked {kind.value} {credential_id}."),
    )


# -- service accounts --------------------------------------------------------


@accounts_app.command("ls")
def list_service_accounts(org: str | None = _org_opt, json_out: bool = _json_opt) -> None:
    """List service accounts."""
    with get_sdk() as sdk:
        rows = sdk.org.service_accounts.list(org_id=org)

    def render(items: list[dict[str, Any]]) -> None:
        if not items:
            err_console.print("No service accounts.")
            return
        print_table(
            ["User ID", "Name", "Role", "Active", "Created"],
            [
                [
                    str(a.get("user_id") or ""),
                    short(a.get("name"), 32),
                    str(a.get("role") or ""),
                    "yes" if a.get("active") else "no",
                    str(a.get("created_at") or "")[:10],
                ]
                for a in items
            ],
            title="Service accounts",
        )

    emit(rows, json_out=json_out, render=render)


@accounts_app.command("create")
def create_service_account(
    name: str = typer.Argument(..., help="Display name, e.g. 'CI bot'."),
    role: AccountRole = typer.Option(AccountRole.member, "--role", help="Org role."),
    org: str | None = _org_opt,
    json_out: bool = _json_opt,
) -> None:
    """Create a service account (web app only).

    Needs a browser session; use the web app: Organization settings > Developer > Service
    accounts. A `beakr auth login` sign-in or an API key is refused with session_required; this
    command works only against a local development server with `beakr auth dev` (development
    only).
    """
    with get_sdk() as sdk:
        created = sdk.org.service_accounts.create(name, role=role.value, org_id=org)
    emit(
        created,
        json_out=json_out,
        render=lambda d: console.print(
            f"Created service account {d.get('name')} ({d.get('user_id')}). "
            "Mint a key with `beakr org service-accounts key <user_id> --scope ...`."
        ),
    )


@accounts_app.command("disable")
def disable_service_account(
    account_id: str = typer.Argument(..., help="User id of the service account."),
    org: str | None = _org_opt,
    yes: bool = _yes_opt,
    json_out: bool = _json_opt,
) -> None:
    """Disable a service account and revoke every key it owns."""
    confirm_or_require_yes(f"Disable service account {account_id} and revoke its keys?", yes)
    with get_sdk() as sdk:
        result = sdk.org.service_accounts.disable(account_id, org_id=org)
    emit(
        result,
        json_out=json_out,
        render=lambda _: console.print(f"Disabled {account_id}; its keys no longer work."),
    )


@accounts_app.command("key")
def mint_service_account_key(
    account_id: str = typer.Argument(..., help="User id of the service account."),
    scope: list[str] = typer.Option(
        ..., "--scope", "-s", help="A scope to grant; repeat for more (`beakr keys scopes`)."
    ),
    project: list[str] = typer.Option(
        None,
        "--project",
        "-P",
        help="Confine the key to a project id; repeatable.",
        callback=project_ref_callback,
    ),
    project_p: list[str] = typer.Option([], "-p", hidden=True, callback=project_ref_callback),
    name: str | None = typer.Option(None, "--name", help="Key name."),
    expires_in_days: int | None = typer.Option(
        None, "--expires-in-days", min=1, max=365, help="Lifetime (org policy may cap it)."
    ),
    client_name: str = typer.Option(
        KEY_CLIENT,
        "--client",
        help="The client the key is for (a CI system, an integration); recorded on the key.",
    ),
    credentials_file: Path | None = typer.Option(
        None,
        "--credentials-file",
        help="Also save the key as a 0600 service account file; point "
        "BEAKR_SERVICE_ACCOUNT_FILE at it in CI.",
    ),
    org: str | None = _org_opt,
    json_out: bool = _json_opt,
) -> None:
    """Mint a scoped key for a service account (web app only).

    Needs a browser session; use the web app: Organization settings > Developer > Service
    accounts shows the key once; copy it into a CI secret (BEAKR_API_KEY) or a service account
    file (BEAKR_SERVICE_ACCOUNT_FILE). A `beakr auth login` sign-in or an API key is refused
    with session_required; this command works only against a local development server with
    `beakr auth dev` (development only).
    """
    # -p was this option's short flag before -P everywhere; still accepted.
    project = [*(project or []), *(project_p or [])]

    with get_sdk() as sdk:
        created = sdk.org.service_accounts.mint_key(
            account_id,
            scopes=list(scope),
            name=name,
            project_ids=list(project) if project else None,
            expires_in_days=expires_in_days,
            client_name=client_name,
            org_id=org,
        )
        base_url = sdk.base_url
    if credentials_file is not None:
        from beakr_sdk.credentials import write_service_account_file

        written = write_service_account_file(
            credentials_file.expanduser(), created, base_url=base_url, org_id=org
        )
        err_console.print(f"Saved the key to {written} (owner read/write only).")

    def render(data: dict[str, Any]) -> None:
        console.print(f"Key for {account_id} (shown once, store it now):")
        console.print(str(data.get("key")), highlight=False, soft_wrap=True)
        console.print(
            f"[dim]Scopes: {', '.join(data.get('scopes') or [])}; "
            f"expires {str(data.get('expires_at') or 'never')[:10]}[/dim]"
        )

    emit(created, json_out=json_out, render=render)


@app.command("show")
def show_org(org: str | None = _org_opt, json_out: bool = _json_opt) -> None:
    """The organization you act in (or --org): name, slug, type, PHI mode."""
    with get_sdk() as sdk:
        record = sdk.organizations.get(org) if org else sdk.me.active_org()

    def render(data: dict[str, Any]) -> None:
        console.print(f"[bold]{data.get('name')}[/bold] ({data.get('id')})", highlight=False)
        for label, key in (
            ("Slug", "slug"),
            ("Type", "org_type"),
            ("Seat", "seat_type"),
            ("Created", "created_at"),
        ):
            if data.get(key):
                console.print(f"{label + ':':<10}{data[key]}", highlight=False)
        console.print(f"{'PHI mode:':<10}{'on' if data.get('phi_mode') else 'off'}")

    emit(record, json_out=json_out, render=render)


# -- policy ------------------------------------------------------------------


def _render_policy(policy: dict[str, Any]) -> None:
    clients = policy.get("allowed_clients")
    lifetime = policy.get("max_key_lifetime_days")
    console.print(
        f"Personal API keys:   {'allowed' if policy.get('allow_personal_keys') else 'admins only'}"
    )
    console.print(f"Max key lifetime:    {f'{lifetime} days' if lifetime else 'no limit'}")
    console.print(
        "Non-expiring keys:   "
        f"{'allowed' if policy.get('allow_non_expiring_keys') else 'not allowed (90-day default)'}"
    )
    console.print(
        f"Step-up MFA:         {'required' if policy.get('require_step_up_mfa') else 'off'}"
    )
    console.print(f"Allowed clients:     {', '.join(clients) if clients else 'any'} (advisory)")
    approved = policy.get("approved_oauth_clients")
    restricted = approved is not None or clients
    console.print(
        "OAuth apps:          "
        + (f"approved only ({len(approved or [])} approved)" if restricted else "any")
    )


@policy_app.command("show")
def show_policy(org: str | None = _org_opt, json_out: bool = _json_opt) -> None:
    """Show the organization's auth policy."""
    with get_sdk() as sdk:
        policy = sdk.org.policy.get(org_id=org)
    emit(policy, json_out=json_out, render=_render_policy)


@policy_app.command("set")
def set_policy(
    personal_keys: bool | None = typer.Option(
        None, "--personal-keys/--no-personal-keys", help="Let members mint their own keys."
    ),
    max_key_days: int | None = typer.Option(
        None, "--max-key-days", min=0, max=365, help="Cap key lifetime; 0 removes the cap."
    ),
    step_up: bool | None = typer.Option(
        None, "--step-up/--no-step-up", help="Require a fresh second factor for sensitive actions."
    ),
    allowed_client: list[str] = typer.Option(
        None, "--allowed-client", help="Only allow this client; repeatable."
    ),
    any_client: bool = typer.Option(False, "--any-client", help="Remove the client allowlist."),
    restrict_oauth_clients: bool | None = typer.Option(
        None,
        "--restrict-oauth-clients/--any-oauth-client",
        help="Only apps an admin approved in the web app may connect, or any "
        "app a person approves (clears approvals).",
    ),
    step_up_sign_ins: bool | None = typer.Option(
        None,
        "--step-up-sign-ins/--no-step-up-sign-ins",
        help="Also require a fresh second factor to approve CLI sign-ins and MCP apps.",
    ),
    non_expiring_keys: bool | None = typer.Option(
        None,
        "--allow-non-expiring-keys/--no-non-expiring-keys",
        help="Let members create keys that never expire (otherwise keys expire in 90 "
        "days, at most a year).",
    ),
    org: str | None = _org_opt,
    json_out: bool = _json_opt,
) -> None:
    """Change the auth policy; unset options keep their current value (web app only).

    Needs a browser session; use the web app: Organization settings > Developer > Authentication
    policy. A `beakr auth login` sign-in or an API key is refused with session_required; this
    command works only against a local development server with `beakr auth dev` (development
    only).
    """
    if allowed_client and any_client:
        err_console.print("Use either --allowed-client or --any-client, not both.")
        raise typer.Exit(2)
    # Only the options given are sent; the SDK keeps every other field at its
    # stored value (it reads the policy and writes it back), so an omitted
    # option can never reset a protection such as step-up MFA.
    changes: dict = {}
    if personal_keys is not None:
        changes["allow_personal_keys"] = personal_keys
    if max_key_days is not None:
        changes["max_key_lifetime_days"] = max_key_days or None
    if step_up is not None:
        changes["require_step_up_mfa"] = step_up
    if allowed_client:
        changes["allowed_clients"] = list(allowed_client)
    elif any_client:
        changes["allowed_clients"] = None
    if restrict_oauth_clients is not None:
        changes["restrict_oauth_clients"] = restrict_oauth_clients
    if step_up_sign_ins is not None:
        changes["step_up_for_sign_ins"] = step_up_sign_ins
    if non_expiring_keys is not None:
        changes["allow_non_expiring_keys"] = non_expiring_keys
    with get_sdk() as sdk:
        updated = sdk.org.policy.set(**changes, org_id=org)
    emit(updated, json_out=json_out, render=_render_policy)


# -- OAuth clients -------------------------------------------------------------


app.add_typer(credentials_app, name="credentials")
app.add_typer(accounts_app, name="service-accounts")
app.add_typer(policy_app, name="policy")


def register(root: typer.Typer) -> None:
    root.add_typer(app, name="org")
