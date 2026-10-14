"""``beakr api <area> <action>``: one command for every public API operation.

The named commands (``beakr projects create``, ``beakr kb cat``...) are the
friendly way in for common work. This group guarantees the rest: every public,
non-deprecated operation in the contract has a command here, so anything the web
app or the SDKs can do, a script or an agent can do from a terminal (developer
platform W7, the phase-2 exit bar). Commands are generated from the vendored
command table (``beakr_cli.api_manifest``, ``data/operations.json``) and built
lazily, so ``beakr --help`` stays fast.

Each command sends its request through the Python SDK (``Beakr.request``):
credentials, refresh, retries, ``Idempotency-Key`` and typed errors are the
SDK's, and errors exit with the CLI's usual codes (3 auth, 4 not found, 5
conflict). Arguments map onto the contract:

- path parameters are positional arguments, in path order;
- query parameters and the request body's top-level fields are options
  (``--field-name``; a body field whose name a query parameter already uses is
  ``--body-field-name``); list fields repeat (``--tag a --tag b``);
- ``--data/-d`` gives the whole JSON body (text, ``@file.json`` or ``-`` for
  stdin), and options override its fields;
- a multipart upload takes each file field as a path option;
- a paginated list takes ``--all`` to follow every page;
- a file response needs ``--out`` (or a pipe); an event stream prints one JSON
  event per line.
"""

from __future__ import annotations

import json
import sys
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

import click

_TYPES: dict[str, Any] = {
    "string": click.STRING,
    "integer": click.INT,
    "number": click.FLOAT,
    "boolean": click.BOOL,
}


#: Option names every generated command may define for itself.
_RESERVED = {"all", "data", "json", "out", "help"}


@lru_cache(maxsize=1)
def manifest() -> dict[str, Any]:
    text = resources.files("beakr_cli").joinpath("data/operations.json").read_text()
    return json.loads(text)


def operations() -> list[dict[str, Any]]:
    return manifest()["operations"]


def command_name(entry: dict[str, Any]) -> str:
    """``api <area> <action>`` -- how the parity matrix and docs name a command."""
    return f"api {entry['area']} {entry['action']}"


def _flag(name: str) -> str:
    return "--" + name.replace("_", "-")


def _option(
    field: dict[str, Any], dest: str, flag: str, *, required: bool, body: bool
) -> click.Option:
    """One query parameter or body field as a Click option."""
    help_text = field.get("description") or ""
    if field.get("binary"):
        return click.Option(
            [flag, dest],
            type=click.Path(exists=True, dir_okay=False, path_type=Path),
            required=required,
            help=help_text or "File to upload.",
        )
    kind = click.Choice(field["enum"]) if field.get("enum") else _TYPES.get(field["type"])
    if kind is None:  # an object field: only through --data
        raise ValueError("object fields are given through --data")
    if field["type"] == "boolean" and not field.get("multiple"):
        stem = flag[2:]
        return click.Option(
            [f"--{stem}/--no-{stem}", dest],
            default=None,
            required=required,
            help=help_text,
        )
    return click.Option(
        [flag, dest],
        type=kind,
        multiple=bool(field.get("multiple")),
        required=required and not body,
        help=help_text,
    )


def _read_data(value: str | None) -> Any:
    if value is None:
        return None
    if value == "-":
        text = sys.stdin.read()
    elif value.startswith("@"):
        text = Path(value[1:]).read_text()
    else:
        text = value
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise click.BadParameter(f"not valid JSON: {exc}", param_hint="--data") from exc


def build_command(entry: dict[str, Any]) -> click.Command:
    """The Click command for one operation."""
    params: list[click.Parameter] = []
    for param in entry["path_params"]:
        params.append(
            click.Argument(
                [f"p__{param['name']}"],
                type=_TYPES.get(param["type"], click.STRING),
                metavar=param["name"].upper(),
            )
        )
    taken = {p["name"] for p in entry["path_params"]}
    query_dests: dict[str, str] = {}
    for param in entry["query"]:
        if param["type"] == "object":
            continue
        dest = f"q__{param['name']}"
        query_dests[dest] = param["name"]
        taken.add(param["name"])
        params.append(
            _option(
                param,
                dest,
                # A query parameter named like a command option (``all``) is
                # ``--param-all``, so ``--all`` keeps meaning every page.
                _flag(f"param-{param['name']}" if param["name"] in _RESERVED else param["name"]),
                required=param["required"],
                body=False,
            )
        )

    body = entry.get("body")
    field_dests: dict[str, str] = {}
    file_dests: dict[str, str] = {}
    if body:
        for field in body["fields"]:
            if field["type"] == "object":
                continue
            option = field.get("option") or field["name"]
            if field["name"] in taken:
                option = f"body-{field['name']}"
            dest = f"b__{field['name']}"
            params.append(
                _option(field, dest, _flag(option), required=field["required"], body=True)
            )
            (file_dests if field.get("binary") else field_dests)[dest] = field["name"]
        if body["media"] == "json":
            params.append(
                click.Option(
                    ["--data", "-d", "data"],
                    help="The JSON body: text, @file.json, or - for stdin. Options override it.",
                )
            )
    if entry["paginated"]:
        params.append(click.Option(["--all", "all_pages"], is_flag=True, help="Follow every page."))
    if entry["response"] == "bytes":
        params.append(
            click.Option(
                ["--out", "-o", "out"],
                type=click.Path(dir_okay=False, path_type=Path),
                help="Write the file here (required unless output is piped).",
            )
        )
    if entry.get("destructive"):
        params.append(
            click.Option(["--yes", "-y", "yes"], is_flag=True, help="Skip the confirmation prompt.")
        )
    params.append(
        click.Option(["--json", "json_out"], is_flag=True, help="Print JSON (always the format).")
    )

    def callback(**values: Any) -> None:
        _invoke(entry, values, query_dests, field_dests, file_dests)

    lines = [entry["summary"] or entry["op"], "", f"{entry['op']}"]
    if entry.get("scope"):
        lines.append(f"Scope: {entry['scope']}.")
    command = click.Command(
        entry["action"],
        params=params,
        callback=callback,
        help="\n\n".join([lines[0], " ".join(lines[2:])]),
        short_help=(entry["summary"] or entry["op"])[:90],
    )
    command.beakr_operation = entry["op"]  # type: ignore[attr-defined]
    return command


def _invoke(
    entry: dict[str, Any],
    values: dict[str, Any],
    query_dests: dict[str, str],
    field_dests: dict[str, str],
    file_dests: dict[str, str],
) -> None:
    from beakr_sdk._paths import segment

    from beakr_cli.output import print_json
    from beakr_cli.sdk import get_sdk

    path = entry["path"]
    for param in entry["path_params"]:
        try:
            # ``.``, ``..`` and empty would be collapsed by the HTTP client and
            # reach another route (audit C057): refused, never rewritten.
            value = segment(values[f"p__{param['name']}"])
        except ValueError as exc:
            raise click.BadParameter(str(exc), param_hint=param["name"].upper()) from exc
        path = path.replace("{" + param["name"] + "}", value)
    query = {
        name: (list(values[dest]) if isinstance(values[dest], tuple) else values[dest])
        for dest, name in query_dests.items()
        if values.get(dest) not in (None, ())
    }

    request: dict[str, Any] = {}
    body = entry.get("body")
    if body and body["media"] == "multipart":
        fields = {
            name: _form_value(values[dest])
            for dest, name in field_dests.items()
            if values.get(dest) not in (None, ())
        }
        files = {
            name: (
                Path(values[dest]).name,
                Path(values[dest]).read_bytes(),
                _content_type(Path(values[dest])),
            )
            for dest, name in file_dests.items()
            if values.get(dest) is not None
        }
        request.update(fields=fields, files=files)
    elif body:
        data = _read_data(values.get("data"))
        overrides = {
            name: (list(values[dest]) if isinstance(values[dest], tuple) else values[dest])
            for dest, name in field_dests.items()
            if values.get(dest) not in (None, ())
        }
        if overrides:
            if data is None:
                data = {}
            if not isinstance(data, dict):
                raise click.UsageError("--data must be a JSON object to combine with field options")
            data = {**data, **overrides}
        missing = [
            f["name"]
            for f in body["fields"]
            if f["required"] and not (isinstance(data, dict) and f["name"] in data)
        ]
        if body["required"] and data is None:
            raise click.UsageError(
                "This command needs a request body: pass --data or the field options."
            )
        if missing:
            flags = ", ".join(_flag(name) for name in missing)
            raise click.UsageError(
                f"Missing required body field(s): {flags} (or include them in --data)."
            )
        if data is not None:
            request["json"] = data

    if entry.get("destructive"):
        from beakr_cli.prompts import confirm_or_require_yes

        confirm_or_require_yes(
            f"{entry['summary'] or entry['op']}: this cannot be undone. Continue?",
            values.get("yes", False),
        )

    kind = entry["response"]
    # An operation the contract marks anonymous (``security: []``) is sent
    # without a credential, so it works before sign-in and never leaks one
    # (audit C059).
    request["authenticated"] = not entry.get("anonymous")
    with get_sdk() as sdk:
        if entry["paginated"] and values.get("all_pages") and not entry.get("anonymous"):
            print_json(list(sdk.paginate(path, **query)))
            return
        if kind == "sse":
            for event in sdk.request(
                entry["method"], path, params=query, response="sse", **request
            ):
                sys.stdout.write(json.dumps(event, default=str) + "\n")
                sys.stdout.flush()
            return
        result = sdk.request(entry["method"], path, params=query, response=kind, **request)
    if kind == "bytes":
        _write_file(result, values.get("out"))
    elif kind == "none":
        print_json({"ok": True})
    elif kind == "text":
        sys.stdout.write(str(result))
    else:
        print_json(result)


def _content_type(path: Path) -> str:
    """The file's type from its name, as a browser upload sends it.

    The engine checks an upload's declared type (a logo must be an image, an
    import a zip); sending everything as ``application/octet-stream`` made the
    CLI's upload differ from the web app's for the same file.
    """
    import mimetypes

    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _form_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, tuple):
        return json.dumps(list(value))
    return str(value)


def _write_file(content: bytes, out: Path | None) -> None:
    from beakr_cli.output import err_console, is_piped

    if out is not None:
        out.write_bytes(content)
        err_console.print(f"Wrote {out} ({len(content):,} bytes)", highlight=False)
        return
    if not is_piped():
        raise click.UsageError("This command returns a file: pass --out PATH or pipe the output.")
    sys.stdout.buffer.write(content)


class _AreaGroup(click.Group):
    """The commands of one area, built when that area is used."""

    def __init__(self, area: str, entries: list[dict[str, Any]]) -> None:
        super().__init__(area, help=f"Every public `{area}` operation of the Beakr API.")
        self._entries = {entry["action"]: entry for entry in entries}

    def list_commands(self, ctx: click.Context) -> list[str]:
        return sorted(self._entries)

    def get_command(self, ctx: click.Context, name: str) -> click.Command | None:
        entry = self._entries.get(name)
        return build_command(entry) if entry else None


class ApiGroup(click.Group):
    """``beakr api``: areas, each built lazily from the command table."""

    def __init__(self) -> None:
        super().__init__(
            "api",
            help=(
                "Every public Beakr API operation as a command: beakr api <area> <action>. "
                "Path parameters are arguments; query parameters and body fields are options; "
                "--data takes a whole JSON body."
            ),
        )

    def _areas(self) -> dict[str, list[dict[str, Any]]]:
        areas: dict[str, list[dict[str, Any]]] = {}
        for entry in operations():
            areas.setdefault(entry["area"], []).append(entry)
        return areas

    def list_commands(self, ctx: click.Context) -> list[str]:
        return sorted(self._areas())

    def get_command(self, ctx: click.Context, name: str) -> click.Command | None:
        entries = self._areas().get(name)
        return _AreaGroup(name, entries) if entries else None


def generated_commands() -> dict[str, str]:
    """``"api <area> <action>"`` -> ``"METHOD /path"`` for every generated command."""
    return {command_name(entry): entry["op"] for entry in operations()}
