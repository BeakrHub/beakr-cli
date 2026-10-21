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
    KnowledgeCommandError,
    NotFoundError,
    PermissionDeniedError,
)
from rich.markup import escape
from typer.core import TyperGroup

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_AUTH = 3
EXIT_NOT_FOUND = 4
EXIT_CONFLICT = 5

#: Knowledge refusals of the caller's arguments (a malformed or unknown argument,
#: a citation that names nothing citeable): usage errors, like Click's own.
_ARGUMENT_REFUSALS = frozenset(
    {"invalid_arguments", "unknown_argument", "invalid_citations", "citation_not_citeable"}
)


def exit_code_for(exc: BaseException) -> int:
    if isinstance(exc, KnowledgeCommandError) and exc.code in _ARGUMENT_REFUSALS:
        return EXIT_USAGE
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
        "The proposal changed after it was reviewed, so nothing was applied. Read it again "
        "(beakr kb proposals show), review what it now proposes, and accept it with "
        "--reviewed-hash and the new content hash only if it is still wanted."
    ),
    "review_required": (
        "Accepting this proposal needs the content hash of the read you reviewed. Review it "
        "with beakr kb proposals show, then accept it with --reviewed-hash and that hash."
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
    "last_owner": (
        "It must keep an owner. Make another member an owner first "
        "(for a project: beakr projects members add PROJECT USER_ID --role owner), "
        "then change or remove this one."
    ),
    "concurrency_limited": (
        "Too many runs are in flight for this key or organization. "
        "Wait for one to finish, then retry."
    ),
    "storage_budget_exceeded": (
        "Your organization imported its daily budget of conversation text. "
        "Imports resume after midnight UTC."
    ),
}


def not_provided_message(exc: BaseException, command: tuple[str, ...] = ()) -> str | None:
    """The "does not provide" sentence when ``exc`` is for a route this server lacks."""
    if not isinstance(exc, NotFoundError):
        return None
    from beakr_cli.capabilities import not_provided

    # A route this installation does not have (a self-hosted one leaves out
    # organization administration, integrations, embedded mode...).
    return not_provided(exc, command)


def _refusal_text(exc: KnowledgeCommandError) -> str:
    """A Knowledge refusal with its code: the door answers it in a 200 body, not as HTTP."""
    text = f"{exc.message} ({exc.code})" if exc.code else exc.message
    return f"{text} To fix: {exc.fix}" if exc.fix else text


def describe_error(exc: BaseException, command: tuple[str, ...] = ()) -> str:
    """One readable line, with the fix when there is an obvious one.

    ``command`` is the CLI command's words, to name what a missing route was for.
    """
    message = not_provided_message(exc, command)
    if message:
        return message
    if isinstance(exc, KnowledgeCommandError):
        return _refusal_text(exc)
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
        json_requested = _json_requested(ctx)
        # Click empties these as it dispatches; they name the command if it fails.
        args = [*(getattr(ctx, "_protected_args", None) or []), *ctx.args]
        try:
            return super().invoke(ctx)
        except HANDLED_EXCEPTIONS as exc:
            from beakr_cli.output import err_console

            code = exit_code_for(exc)
            command = self._command_words(ctx, args)
            if json_requested:
                _print_json_error(exc, code, command)
            err_console.print(
                f"[red]Error:[/red] {escape(str(describe_error(exc, command)))}", highlight=False
            )
            raise typer.Exit(code) from exc
        except ValueError as exc:
            if not _is_path_value_refusal(exc):
                raise
            # An empty, "." or ".." id (`beakr kb proposals show ""`) is refused by the
            # SDK before anything is sent; it is the caller's argument, so a usage error.
            usage = click.UsageError(f"An id or name argument is empty or invalid ({exc}).", ctx)
            if json_requested:
                _print_json_error(usage, usage.exit_code)
            raise usage from exc
        except click.ClickException as exc:
            # A usage error (exit 2) under --json is a JSON error too; Click
            # still prints its usage text to stderr.
            if json_requested:
                _print_json_error(exc, exc.exit_code)
            raise

    def _command_words(self, ctx: click.Context, args: list[str]) -> tuple[str, ...]:
        """The words of ``args`` naming the invoked command (``("members", "ls")``)."""
        words: list[str] = []
        command: click.Command | None = self
        for arg in args:
            if not isinstance(command, click.Group):
                break
            try:
                command = command.get_command(ctx, arg)
            except Exception:  # naming the command must never hide the error
                break
            if command is None:
                break
            words.append(arg)
        return tuple(words)


def _is_path_value_refusal(exc: ValueError) -> bool:
    """Whether the SDK's path guard (``beakr_sdk._paths.segment``) raised ``exc``."""
    tb = exc.__traceback__
    while tb is not None and tb.tb_next is not None:
        tb = tb.tb_next
    return tb is not None and tb.tb_frame.f_globals.get("__name__") == "beakr_sdk._paths"


def _json_requested(ctx: click.Context) -> bool:
    """Whether this invocation asked for JSON (``--json``/``-j`` or ``BEAKR_OUTPUT=json``)."""
    import os

    if os.environ.get("BEAKR_OUTPUT", "").strip().lower() == "json":
        return True
    remaining = [*(getattr(ctx, "_protected_args", None) or []), *ctx.args]
    if "--" in remaining:
        remaining = remaining[: remaining.index("--")]
    return "--json" in remaining or "-j" in remaining


#: The code an error without one from the API carries, by exit code.
_CODE_FOR_EXIT = {EXIT_AUTH: "unauthorized", EXIT_NOT_FOUND: "not_found", EXIT_CONFLICT: "conflict"}


def error_object(
    exc: BaseException, exit_code: int, command: tuple[str, ...] = ()
) -> dict[str, Any]:
    """The JSON a failed ``--json`` command prints on stdout instead of nothing.

    A script reading stdout gets one JSON document either way; with the error
    it can branch on ``code`` (the API's machine-readable code, when it sent
    one) as well as on the exit code. A route this installation does not
    have is ``not_provided`` with the sentence the plain output prints.
    """
    absent = not_provided_message(exc, command)
    if absent:
        from beakr_cli.capabilities import NOT_PROVIDED_CODE

        lacking: dict[str, Any] = {
            "message": absent,
            "exit_code": exit_code,
            "code": NOT_PROVIDED_CODE,
        }
        if isinstance(exc, BeakrError) and exc.request_id:
            lacking["request_id"] = exc.request_id
        return {"error": lacking}
    message = getattr(exc, "message", None) or (
        exc.format_message() if isinstance(exc, click.ClickException) else str(exc)
    )
    error: dict[str, Any] = {"message": str(message), "exit_code": exit_code}
    if isinstance(exc, BeakrError):
        error["code"] = exc.code or _CODE_FOR_EXIT.get(exit_code, "error")
        # A Knowledge refusal arrives in a 200 body: no HTTP status refused it.
        if exc.status is not None and not isinstance(exc, KnowledgeCommandError):
            error["status"] = exc.status
        if exc.fix or exc.code in _CODE_HINTS:
            error["fix"] = exc.fix or _CODE_HINTS[str(exc.code)]
        if exc.request_id:
            error["request_id"] = exc.request_id
    elif isinstance(exc, click.UsageError):
        error["code"] = "usage"
    elif isinstance(exc, (FileNotFoundError, NotADirectoryError)):
        error["code"] = "file_not_found"
    return {"error": error}


def _print_json_error(exc: BaseException, exit_code: int, command: tuple[str, ...] = ()) -> None:
    from beakr_cli.output import print_json

    print_json(error_object(exc, exit_code, command))
