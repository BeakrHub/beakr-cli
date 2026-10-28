"""Auth commands: login (browser, device or API key), status, token, logout, whoami, dev, scope."""

from __future__ import annotations

import os
import sys
import webbrowser
from typing import Any

import typer
from rich.markup import escape

from beakr_cli import config
from beakr_cli.auth_status import auth_status_report
from beakr_cli.commands.auth_token import token as _token_command
from beakr_cli.output import (
    console,
    emit,
    err_console,
    human_output_to_stderr,
    is_interactive,
    print_json,
)
from beakr_cli.project_ref import project_ref_callback
from beakr_cli.prompts import (
    confirm_if_interactive,
    prompt_choice,
    prompt_secret,
    require_interactive,
)

app = typer.Typer(help="Authenticate with Beakr.")


def _me() -> dict[str, Any]:
    """``GET /v1/me``: who the current credential signs in as."""
    from beakr_cli.sdk import get_sdk

    with get_sdk() as sdk:
        return sdk.me.get()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_json_opt = typer.Option(False, "--json", "-j", help="Output raw JSON.")


def _pick_project() -> dict[str, Any]:
    """Fetch projects from the API and prompt the user to select one; return the choice."""
    require_interactive("--project")
    from beakr_cli.sdk import get_sdk

    try:
        with get_sdk() as sdk:
            projects = sdk.projects.list()
    except Exception as exc:
        err_console.print(f"Failed to fetch projects: {exc}", markup=False)
        raise typer.Exit(1)

    if not projects:
        err_console.print("No projects found in this organization.")
        raise typer.Exit(1)

    console.print()
    console.print("[bold]Available projects:[/bold]")
    for i, p in enumerate(projects, 1):
        name = p.get("name", "Untitled")
        desc = p.get("description", "") or ""
        suffix = f" -- {desc}" if desc else ""
        console.print(f"  {i}. {name}{suffix}", markup=False)

    console.print()
    selected = projects[prompt_choice("Select a project", len(projects), flag_hint="--project")]
    project_id = str(selected["id"])
    project_name = selected.get("name", "")

    config.set_key("project_id", project_id)
    console.print(
        f"Default project: [bold]{escape(str(project_name))}[/bold] ({escape(str(project_id))})"
    )
    return {"project_id": project_id, "name": project_name}


def _identity_line(data: dict[str, Any]) -> tuple[str, str]:
    user = data.get("user", data)
    org = data.get("personal_org", data)
    identity = user.get("display_name") or user.get("primary_email") or "unknown"
    org_name = org.get("name") or org.get("slug") or "unknown"
    return identity, org_name


_ENV_URLS = {
    "prod": "https://api.thebeakr.com",
    "sandbox": "https://api-sandbox.thebeakr.com",
}

#: Config keys that select a credential. Logout clears all of them.
_CREDENTIAL_CONFIG_KEYS = (
    "api_key",
    "dev_identity_id",
    "dev_email",
    "dev_display_name",
    "dev_identity_name",
    "dev_origin",
)


def _resolve_login_url(env: str | None, api_url: str | None) -> str | None:
    """The base URL a login targets, or None to keep the configured one."""
    if api_url:
        return api_url.rstrip("/")
    if env is None:
        return None
    resolved = _ENV_URLS.get(env)
    if not resolved:
        err_console.print(f"Unknown environment: {env}. Use 'prod' or 'sandbox'.", markup=False)
        raise typer.Exit(2)
    return resolved


def _read_key_from_stdin_or_prompt() -> str:
    if is_interactive():
        return prompt_secret("API key", flag_hint="--with-api-key (key on stdin)")
    key = sys.stdin.read().strip()
    if not key:
        err_console.print("No API key on stdin.")
        raise typer.Exit(2)
    return key


def _login_with_api_key(api_key: str, base_url: str) -> dict[str, Any]:
    from beakr_sdk.credentials import TokenStore

    config.set_key("api_url", base_url)
    # The key goes to the token store (keychain, else a 0600 file), never to
    # config.json, and takes the place of this API's sign-in, which would
    # otherwise outrank it.
    config.save_api_key(api_key, base_url)
    try:
        data = _me()
    except Exception as exc:
        err_console.print(f"Authentication failed: {exc}", markup=False)
        TokenStore().clear(base_url)
        raise typer.Exit(3) from exc
    identity, org_name = _identity_line(data)
    console.print(
        f"Authenticated as [bold]{escape(str(identity))}[/bold] in "
        f"[bold]{escape(str(org_name))}[/bold]"
    )
    return {"identity": identity, "org": org_name}


def _login_with_oauth(base_url: str, *, open_browser: bool, device: bool) -> dict[str, Any]:
    """Sign in with the OAuth issuer the API names (``GET /v1/auth/config``).

    The default is the browser: an authorization-code flow with PKCE whose
    redirect lands on a listener on ``127.0.0.1``. ``device`` uses the issuer's
    device grant instead, when it offers one.
    """
    from beakr_sdk.credentials import StoredTokens, TokenStore

    from beakr_cli._private_api import PrivateBeakr
    from beakr_cli._private_api.oauth import OAuthLoginError
    from beakr_cli.sdk import CLI_CLIENT_NAME

    def show_url(url: str) -> None:
        if open_browser:
            err_console.print("Opening your browser to sign in. If it does not open, visit:")
        else:
            err_console.print("Open this link in a browser on this machine to sign in:")
        err_console.print(url, highlight=False, soft_wrap=True, markup=False)
        if open_browser:
            try:
                webbrowser.open(url)
            except Exception:
                pass
        err_console.print("[dim]Waiting for the browser...[/dim]")

    # The sign-in configuration is an unlisted route: the private client.
    try:
        with PrivateBeakr(base_url=base_url, client_name=CLI_CLIENT_NAME) as sdk:
            auth_config = sdk.auth.config()
            metadata = sdk.auth.issuer_metadata(auth_config.issuer)
            if device:
                grant = sdk.auth.start_device_login(auth_config, metadata)
                url = grant.get("verification_uri_complete") or grant.get("verification_uri")
                err_console.print(
                    f"Your one-time code: [bold]{escape(str(grant['user_code']))}[/bold]\n"
                    f"Confirm it in a browser: {escape(str(url))}"
                )
                if open_browser and url:
                    try:
                        webbrowser.open(str(url))
                    except Exception:
                        pass
                err_console.print("[dim]Waiting for approval...[/dim]")
                token_response = sdk.auth.wait_for_device_token(grant)
            else:
                token_response = sdk.auth.browser_login(auth_config, metadata, open_url=show_url)
    except OAuthLoginError as exc:
        err_console.print(str(exc), markup=False)
        raise typer.Exit(3) from exc

    tokens = StoredTokens.from_token_response(
        token_response, base_url=base_url, client_name=CLI_CLIENT_NAME
    )
    tokens.issuer = metadata.issuer
    tokens.token_endpoint = metadata.token_endpoint
    tokens.revocation_endpoint = metadata.revocation_endpoint
    tokens.oauth_client_id = auth_config.client_id
    store = TokenStore()
    store.save(tokens)
    # A dev identity outranks a stored sign-in in the credential chain; a real
    # login means the user is done with local-dev mode. A saved API key is
    # left in place: the sign-in already outranks it, and it remains the fallback.
    for key in (
        "dev_identity_id",
        "dev_email",
        "dev_display_name",
        "dev_identity_name",
        "dev_origin",
    ):
        config.delete_key(key)

    user = token_response.get("user") or {}
    identity = user.get("display_name") or user.get("primary_email") or user.get("email")
    if not identity:
        try:
            identity, _ = _identity_line(_me())
        except Exception:
            identity = "unknown"
    console.print(
        f"Signed in as [bold]{escape(str(identity))}[/bold]. Credentials saved to the "
        f"{escape(str(store.backend))}."
    )
    return {"identity": identity, "credential_store": store.backend}


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command()
def login(
    api_key: str | None = typer.Option(
        None,
        "--api-key",
        # Still accepted, not documented: a key on the command line lands in
        # shell history and the process list. --with-api-key reads it from stdin.
        hidden=True,
        help="Save this API key (prefer --with-api-key, which keeps it out of shell history).",
    ),
    with_api_key: bool = typer.Option(
        False,
        "--with-api-key",
        help="Sign in with an API key: read from stdin when piped, else a hidden prompt.",
    ),
    env: str | None = typer.Option(None, "--env", "-e", help="Environment: prod or sandbox."),
    api_url: str | None = typer.Option(None, help="Custom API base URL (overrides --env)."),
    no_browser: bool = typer.Option(
        False, "--no-browser", help="Print the sign-in link without opening a browser."
    ),
    device: bool = typer.Option(
        False,
        "--device",
        help="Sign in with a one-time code from another device (no local browser needed).",
    ),
    json_out: bool = _json_opt,
) -> None:
    """Sign in through your browser, or with --device or --with-api-key.

    With a key: `echo "$BEAKR_KEY" | beakr auth login --with-api-key` (or run it
    at a terminal for a hidden prompt). The key is saved in the OS keychain,
    else ~/.beakr/credentials (0600).

    The browser sign-in opens your organization's sign-in page and returns to a
    one-time listener on 127.0.0.1; --no-browser prints the link instead.
    """
    from beakr_sdk.credentials import resolve_base_url

    explicit_url = _resolve_login_url(env, api_url)
    base_url = explicit_url or resolve_base_url()
    if explicit_url:
        config.set_key("api_url", explicit_url)

    with human_output_to_stderr(json_out):
        if with_api_key and not api_key:
            api_key = _read_key_from_stdin_or_prompt()
        if api_key:
            signed_in = {"method": "api_key", **_login_with_api_key(api_key, base_url)}
        else:
            signed_in = {
                "method": "device" if device else "browser",
                **_login_with_oauth(base_url, open_browser=not no_browser, device=device),
            }
        # A --json caller is a script: the optional project question is skipped.
        if not json_out and confirm_if_interactive("Select a default project?", default=False):
            signed_in["default_project"] = _pick_project()

    if json_out:
        print_json({"signed_in": True, "base_url": base_url, **signed_in})


_SOURCE_LABELS = {
    "explicit": "explicit key",
    "dev": "local dev identity",
    "env": "BEAKR_API_KEY",
    "cli_tokens": "browser sign-in",
    "cli_config": "saved API key",
}


@app.command()
def status(
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
) -> None:
    """Show which credential is active and where it is stored."""
    report = auth_status_report()

    def render(data: dict[str, Any]) -> None:
        console.print(f"Base URL:  {data['base_url']}", markup=False)
        if not data["authenticated"]:
            console.print("Signed in: [red]no[/red] (run beakr auth login)")
            return
        console.print(
            f"Signed in: yes, via {_SOURCE_LABELS.get(data['source'], data['source'])}",
            markup=False,
        )
        if data.get("issuer") and data.get("source") == "cli_tokens":
            console.print(f"Issuer:    {data['issuer']}", highlight=False, markup=False)
        if data.get("access_expires_at"):
            state = " (expired; refreshes on next use)" if data.get("access_expired") else ""
            console.print(f"Access:    expires {data['access_expires_at']}{state}", markup=False)
            console.print(
                f"Refresh:   expires {data.get('refresh_expires_at') or 'unknown'}", markup=False
            )
            console.print(f"Stored in: {data['token_backend']}", markup=False)
        if data.get("key_prefix"):
            console.print(f"Key:       {data['key_prefix']}...", markup=False)
        if data.get("org_override"):
            console.print(f"Org:       {data['org_override']}", markup=False)

    emit(report, json_out=json_out, render=render)
    if not report["authenticated"]:
        raise typer.Exit(3)


def _revoke_stored_tokens(origin: str | None) -> bool:
    """Revoke the stored sign-in for ``origin`` (every sign-in when None) at its issuer.

    Never fails logout. Sign-ins are filed per API origin; each is revoked at
    the issuer that signed it in.
    """
    from beakr_sdk.credentials import TokenStore, normalize_origin

    revoked = False
    for login_origin, tokens in TokenStore().logins().items():
        if origin is not None and login_origin != normalize_origin(origin):
            continue
        revoked = _revoke_login(tokens) or revoked
    return revoked


def _revoke_login(tokens) -> bool:
    """Revoke the refresh token at the issuer (RFC 7009), which ends the sign-in."""
    from beakr_sdk.errors import BeakrError

    from beakr_cli._private_api import PrivateBeakr

    if not tokens.token_endpoint:
        # Signed in by Beakr's own device flow, which the API no longer serves.
        return False
    try:
        with PrivateBeakr(base_url=tokens.base_url, api_key="unused") as sdk:
            endpoint = tokens.revocation_endpoint
            if not endpoint and tokens.issuer.startswith(("https://", "http://")):
                endpoint = sdk.auth.issuer_metadata(tokens.issuer).revocation_endpoint
            if not endpoint:
                err_console.print(
                    "[dim]The sign-in issuer has no revocation endpoint; the sign-in "
                    "lapses when its refresh token expires.[/dim]"
                )
                return False
            sdk.auth.revoke_at_issuer(
                endpoint, tokens.refresh_token, client_id=tokens.oauth_client_id or ""
            )
    except BeakrError as exc:
        err_console.print(
            f"[yellow]Could not revoke the sign-in at its issuer: {escape(str(exc))}[/yellow]"
        )
        return False
    return True


@app.command()
def logout(
    all_logins: bool = typer.Option(
        False,
        "--all",
        help="Sign out of every API this machine holds a Beakr login for, not just this one.",
    ),
    json_out: bool = _json_opt,
) -> None:
    """Revoke the sign-in for the current API at its issuer and remove this CLI's credentials.

    Sign-ins live in one OS keychain item shared by every
    ``BEAKR_HOME`` on the machine, one per API origin. Logout removes only the
    current API's login unless ``--all`` is given; it used to revoke and delete
    every login in that shared item, signing out other profiles and servers.
    """
    from beakr_sdk.credentials import CredentialClearError, TokenStore, resolve_base_url

    origin = None if all_logins else resolve_base_url()
    revoked = _revoke_stored_tokens(origin)
    try:
        outcome = TokenStore().clear(origin)
    except CredentialClearError as exc:
        # Nothing was removed or recorded: saying "Logged out" would be false,
        # and the next command would still authenticate (audit C080).
        err_console.print(f"Not logged out: {exc}", markup=False)
        raise typer.Exit(1) from exc
    for key in (*_CREDENTIAL_CONFIG_KEYS, "org_id", "project_id"):
        config.delete_key(key)
    if json_out:
        print_json(
            {
                "logged_out": True,
                "scope": "all" if all_logins else origin,
                "outcome": outcome,
                "tokens_revoked": revoked,
            }
        )
        return
    if outcome == "cleared_locally":
        console.print(
            "Logged out in this Beakr profile (BEAKR_HOME). The keychain could not "
            "confirm removing the stored login, so it is blocked in this profile and will "
            "be removed the next time the keychain is readable. Other Beakr profiles that "
            "share this keychain keep the login until then."
            + (" Tokens revoked." if revoked else "")
        )
        return
    console.print("Logged out." + (" Tokens revoked." if revoked else ""))


def _whoami(json_out: bool) -> None:
    from beakr_cli.errors import exit_code_for

    try:
        data = _me()
    except Exception as exc:
        err_console.print(f"Failed: {exc}", markup=False)
        # The CLI's usual codes: an expired, revoked or missing credential is 3
        # (authentication), as `beakr auth status` answers it. Every failure
        # used to exit 1, so a script could not tell "sign in again" from an
        # outage.
        raise typer.Exit(exit_code_for(exc)) from exc
    active_org = os.environ.get("BEAKR_ORG_ID") or config.get("org_id")
    scope_project = os.environ.get("BEAKR_PROJECT_ID") or config.get("project_id")
    if json_out:
        print_json({**data, "org_override": active_org, "project_scope": scope_project})
        return
    identity, org_name = _identity_line(data)
    console.print(f"User:  {identity}", markup=False)
    console.print(f"Org:   {org_name}", markup=False)
    if active_org:
        console.print(f"Org override: {active_org}", markup=False)
    if scope_project:
        console.print(f"Scope: project {scope_project}", markup=False)
    else:
        console.print("Scope: [dim]none -- org-wide (run beakr auth set-scope)[/dim]")


@app.command()
def whoami(
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
) -> None:
    """Show current user and org."""
    _whoami(json_out)


@app.command()
def dev(
    identity: str = typer.Option(..., "--identity", "-i", help="Dev identity ID for local API."),
    email: str = typer.Option(..., "--email", "-e", help="Dev email for local API."),
    display_name: str = typer.Option(None, "--name", "-n", help="Display name."),
    api_url: str = typer.Option("http://localhost:8000", help="Local API URL."),
    json_out: bool = _json_opt,
) -> None:
    """Development only: act as a chosen identity against a local dev-mode API.

    Sends identity headers that only a development or test server accepts; the
    hosted API never does. Optionally selects a default project.
    """
    from beakr_sdk.credentials import normalize_origin

    config.set_key("dev_identity_id", identity)
    config.set_key("dev_email", email)
    config.set_key("api_url", api_url)
    # The saved identity is bound to this API: the SDK offers it only to a
    # client pointed at the same origin, never to production.
    config.set_key("dev_origin", normalize_origin(api_url))
    if display_name:
        config.set_key("dev_display_name", display_name)
    # Clear prod API key so dev headers take priority
    config.delete_key("api_key")
    result: dict[str, Any] = {"dev_identity_id": identity, "email": email, "api_url": api_url}
    with human_output_to_stderr(json_out):
        console.print(f"Dev mode: {identity} ({email}) -> {api_url}", markup=False)
        # A --json caller is a script: the optional project question is skipped.
        if not json_out and confirm_if_interactive("Select a default project?", default=True):
            result["default_project"] = _pick_project()
        else:
            config.delete_key("project_id")
            result["default_project"] = None
            console.print("No default scope set (org-wide).")
    if json_out:
        print_json(result)


@app.command("set-scope")
def set_scope(
    project_id: str | None = typer.Option(
        None,
        "--project",
        "-P",
        "--scope",
        help="Project id, slug or name (skip interactive picker).",
        callback=project_ref_callback,
    ),
    project_id_p: str | None = typer.Option(None, "-p", hidden=True, callback=project_ref_callback),
    json_out: bool = _json_opt,
) -> None:
    """Change the default project scope."""
    # -p was this option's short flag before -P everywhere; still accepted.
    project_id = project_id or project_id_p

    with human_output_to_stderr(json_out):
        if project_id:
            config.set_key("project_id", project_id)
            console.print(f"Scope set to project {project_id}", markup=False)
        else:
            # Refuses (exit 2, naming --project) when not running interactively.
            project_id = _pick_project()["project_id"]
    if json_out:
        print_json({"project_id": project_id})


@app.command("set-org")
def set_org(
    org_id: str = typer.Argument(..., help="Organization UUID or slug for X-Org-Id."),
    json_out: bool = _json_opt,
) -> None:
    """Set the active organization for CLI and MCP requests."""
    config.set_key("org_id", org_id)
    emit(
        {"org_id": org_id},
        json_out=json_out,
        render=lambda _: console.print(f"Org set to {org_id}", markup=False),
    )


@app.command("clear-org")
def clear_org(json_out: bool = _json_opt) -> None:
    """Clear the active organization override."""
    config.delete_key("org_id")
    emit(
        {"org_id": None, "cleared": True},
        json_out=json_out,
        render=lambda _: console.print("Org override cleared."),
    )


app.command("token")(_token_command)


def register(root: typer.Typer) -> None:
    """Attach ``beakr auth ...`` and the top-level ``beakr whoami``."""
    root.add_typer(app, name="auth")

    @root.command("whoami")
    def top_level_whoami(
        json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
    ) -> None:
        """Show current user and org."""
        _whoami(json_out)
