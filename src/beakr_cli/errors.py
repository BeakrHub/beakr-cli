"""Stable exit codes, and the root command group that maps errors onto them.

Scripts and agents branch on the exit code, so it is part of the CLI's
contract: 0 ok, 1 error, 2 usage, 3 authentication or permission, 4 not found,
5 conflict. Usage errors (2) come from Click itself.
"""

from __future__ import annotations

from typing import Any

import click
import typer
from beakr_sdk.errors import (
    AuthenticationError,
    BeakrError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from typer.core import TyperGroup

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_AUTH = 3
EXIT_NOT_FOUND = 4
EXIT_CONFLICT = 5


def exit_code_for(exc: BaseException) -> int:
    if isinstance(exc, (AuthenticationError, PermissionDeniedError)):
        return EXIT_AUTH
    if isinstance(exc, NotFoundError):
        return EXIT_NOT_FOUND
    if isinstance(exc, ConflictError):
        return EXIT_CONFLICT
    if isinstance(exc, BeakrError):
        if exc.code and "not_found" in exc.code:
            return EXIT_NOT_FOUND
        return EXIT_ERROR
    if isinstance(exc, FileNotFoundError):
        return EXIT_NOT_FOUND
    return EXIT_ERROR


#: The fix for refusals the API states with a specific ``error.code``.
_CODE_HINTS = {
    "proposal_changed": (
        "The proposal changed after it was read, so nothing was applied. Read it again "
        "(beakr kb proposals show), review what it now proposes, and accept only if it is "
        "still wanted."
    ),
    "session_required": (
        "This needs a signed-in browser session; API keys and CLI tokens cannot do it. "
        "Do it in the Beakr web app."
    ),
    "mfa_required": (
        "Your organization requires a recently verified second factor for this. "
        "Verify again in the Beakr web app (sign out and back in), then retry "
        "within 10 minutes from the browser."
    ),
    "mfa_enrollment_required": (
        "This needs a second factor, and your account does not have one yet. "
        "Add one (an authenticator app or a passkey) in the Beakr web app under "
        "Settings, Profile, Manage Account, then retry."
    ),
    "client_not_allowed": (
        "Your organization's auth policy only allows certain clients. "
        "Ask an admin (the web app's Organization settings > Developer lists them)."
    ),
    "sso_required": (
        "Your organization requires signing in through its single sign-on for this. "
        "Sign out of the Beakr web app, sign back in with your organization's SSO, "
        "then retry from the browser (or approve the CLI sign-in again)."
    ),
    "forbidden": "This needs a role you do not have here (often organization admin).",
    "concurrency_limited": (
        "Too many runs are in flight for this key or organization. "
        "Wait for one to finish, then retry."
    ),
    "storage_budget_exceeded": (
        "Your organization imported its daily budget of conversation text. "
        "Imports resume after midnight UTC."
    ),
}


def describe_error(exc: BaseException) -> str:
    """One readable line, with the fix when there is an obvious one."""
    if isinstance(exc, BeakrError) and exc.code in _CODE_HINTS:
        return f"{exc}\n{_CODE_HINTS[exc.code]}"
    if isinstance(exc, PermissionDeniedError) and exc.required_scope:
        return (
            f"{exc} -- this credential lacks the '{exc.required_scope}' scope. "
            "Create a key with it in the web app (`beakr keys create` opens the page) "
            "or sign in with `beakr auth login`."
        )
    if isinstance(exc, AuthenticationError):
        return f"{exc}\nRun `beakr auth login` (or set BEAKR_API_KEY)."
    return str(exc)


#: Exceptions the CLI reports as a one-line error rather than a traceback. Every
#: API call goes through the SDK, so API failures arrive as ``BeakrError``.
HANDLED_EXCEPTIONS = (BeakrError, FileNotFoundError, NotADirectoryError)


class BeakrGroup(TyperGroup):
    """Root group: turn API and file errors into a message and a stable exit code.

    It also carries ``beakr api``, the generated command for every public
    operation, as a lazily built Click group (``beakr_cli.commands.api``).
    """

    def list_commands(self, ctx: click.Context) -> list[str]:
        names = list(super().list_commands(ctx))
        return names if "api" in names else [*names, "api"]

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        if cmd_name == "api":
            from beakr_cli.commands.api import ApiGroup

            return ApiGroup()
        return super().get_command(ctx, cmd_name)

    def invoke(self, ctx: click.Context) -> Any:
        try:
            return super().invoke(ctx)
        except HANDLED_EXCEPTIONS as exc:
            from beakr_cli.output import err_console

            err_console.print(f"[red]Error:[/red] {describe_error(exc)}", highlight=False)
            raise typer.Exit(exit_code_for(exc)) from exc
