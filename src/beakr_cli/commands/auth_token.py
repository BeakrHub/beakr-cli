"""``beakr auth token``: print the current access token, refreshed when needed.

This is how SDKs in other languages (the TypeScript SDK in Node.js) use the
CLI's sign-in without reimplementing its storage or its refresh coordination:

- The login may live in the OS keychain (Python ``keyring``), which other
  runtimes cannot read the same way; this command reads it exactly as every
  CLI command does.
- Refreshing a sign-in may rotate it, and presenting a spent refresh token
  revokes the whole login. Every refresh therefore runs under the CLI's
  cross-process lock (``credentials.lock``) and adopts a pair another process
  already rotated. This command refreshes through that same path
  (``Beakr.ensure_fresh_credential``), so a caller never rotates anything
  itself, and a login cleared by ``beakr auth logout`` stays cleared.

Only the access token is printed. The refresh token never leaves the store.
"""

from __future__ import annotations

import hashlib
import hmac
import sys
from typing import Any, NoReturn

import typer

from beakr_cli.errors import EXIT_AUTH, EXIT_ERROR, EXIT_USAGE, exit_code_for
from beakr_cli.output import err_console, print_json


def _fail(code: str, message: str, exit_code: int, *, json_out: bool) -> NoReturn:
    if json_out:
        print_json({"error": {"code": code, "message": message}})
    else:
        err_console.print(f"[red]Error:[/red] {message}", highlight=False)
    raise typer.Exit(exit_code)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def token(
    json_out: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Print {access_token, token_type, expires_at, origin, source, kind} as JSON.",
    ),
    api_url: str | None = typer.Option(
        None,
        "--api-url",
        help="The API the token is for (default: BEAKR_BASE_URL, then the configured API). "
        "A stored sign-in is only ever printed for the API that issued it.",
    ),
    rejected_sha256: str | None = typer.Option(
        None,
        "--rejected-sha256",
        help="SHA-256 (hex) of an access token the API refused. When it is still the "
        "stored one, the sign-in is refreshed now; when the CLI has already replaced "
        "it, the newer token is printed.",
    ),
) -> None:
    """Print the access token for the current API, refreshing the stored sign-in when needed.

    Prints the bare token (for `Authorization: Bearer $(beakr auth token)`),
    or JSON with --json. Exits 3 when there is no credential for the API.
    """
    from beakr_sdk.credentials import RefreshLockTimeout
    from beakr_sdk.errors import AuthenticationError, BeakrError

    from beakr_cli.sdk import get_sdk

    rejected = rejected_sha256.strip().lower() if rejected_sha256 else None
    if rejected is not None and (
        len(rejected) != 64 or any(c not in "0123456789abcdef" for c in rejected)
    ):
        _fail(
            "invalid_argument",
            "--rejected-sha256 must be a 64-character hex SHA-256 digest.",
            EXIT_USAGE,
            json_out=json_out,
        )

    with get_sdk(base_url=api_url) as sdk:
        origin = sdk.origin
        try:
            credential = sdk.credential
            if credential.kind == "dev_identity":
                _fail(
                    "no_bearer_token",
                    "The active credential is a local dev identity, which is sent as "
                    "headers rather than a bearer token.",
                    EXIT_AUTH,
                    json_out=json_out,
                )
            current = credential.tokens.access_token if credential.tokens else credential.token
            refused = (
                current
                if rejected is not None
                and current is not None
                and hmac.compare_digest(_sha256(current), rejected)
                else None
            )
            credential = sdk.ensure_fresh_credential(rejected_access_token=refused)
        except AuthenticationError as exc:
            _fail(exc.code or "not_authenticated", str(exc), EXIT_AUTH, json_out=json_out)
        except RefreshLockTimeout as exc:
            _fail(
                "refresh_lock_timeout",
                f"Another Beakr process is refreshing the sign-in and did not finish: {exc}",
                EXIT_ERROR,
                json_out=json_out,
            )
        except BeakrError as exc:
            _fail(exc.code or "error", str(exc), exit_code_for(exc), json_out=json_out)

    tokens = credential.tokens
    access_token = tokens.access_token if tokens is not None else credential.token
    if not access_token:
        _fail(
            "not_authenticated",
            f"No Beakr credentials for {origin}. Run `beakr auth login`.",
            EXIT_AUTH,
            json_out=json_out,
        )
    if not json_out:
        sys.stdout.write(f"{access_token}\n")
        return
    report: dict[str, Any] = {
        "access_token": access_token,
        "token_type": "Bearer",
        # Epoch seconds, or null for a credential without an expiry (an API key).
        "expires_at": tokens.expires_at if tokens is not None else None,
        "origin": origin,
        "source": credential.source,
        "kind": credential.kind,
    }
    print_json(report)
