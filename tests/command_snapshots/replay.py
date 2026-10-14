"""Replay recorded API responses to the CLI and capture what it sends and prints.

Each file in ``tests/fixtures/recorded_api`` is one public operation's success
response: ``{"operation": "GET /v1/projects/{project_id}", "status": 200,
"content_type": "application/json", "body": ...}`` (``"text"`` instead of
``"body"`` for a non-JSON answer, ``"body": null`` for an empty one), plus a
``"source"`` saying where it came from. The engine writes the same format when its
positive-path suite runs with ``BEAKR_RECORD_FIXTURES`` (see
``tests/api/test_public_api_positive_paths.py`` in beakr-engine), so a recording
replaces a hand-authored file in place.

A command under replay may call any operation it likes; each request is
identified as a public operation by method and path template (from the CLI's
command table, ``beakr_cli/data/operations.json``, which is generated from the
public contract), answered with that operation's recorded response, and logged
so the snapshot shows the requests too. A request no fixture covers is answered
501 and reported, so a command that starts calling a new operation fails until
its response is recorded.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import httpx

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "recorded_api"
API = "https://api.test"
#: Presigned upload and download URLs in the fixtures point here: object storage,
#: not the API, so it is answered without a fixture (a PUT is accepted, a GET
#: returns ``STORED_BYTES``).
STORAGE_HOST = "storage.test"
STORED_BYTES = b"%PDF-1.7\n% Helios charter (snapshot bytes)\n"


@dataclass(frozen=True)
class Fixture:
    operation: str
    status: int
    content_type: str | None
    body: Any
    text: str | None
    source: str
    file: Path

    @property
    def method(self) -> str:
        return self.operation.split(" ", 1)[0]

    @property
    def template(self) -> str:
        return self.operation.split(" ", 1)[1]


def fixture_filename(operation: str, variant: str | None = None) -> str:
    """``GET /v1/projects/{project_id}`` -> ``get__v1_projects_project_id.json``."""
    method, path = operation.split(" ", 1)
    slug = re.sub(r"[^a-z0-9]+", "_", path.lower()).strip("_")
    suffix = f"--{variant}" if variant else ""
    return f"{method.lower()}__{slug}{suffix}.json"


def _load(file: Path) -> Fixture:
    raw = json.loads(file.read_text())
    return Fixture(
        operation=raw["operation"],
        status=raw["status"],
        content_type=raw.get("content_type"),
        body=raw.get("body"),
        text=raw.get("text"),
        source=raw.get("source", ""),
        file=file,
    )


def load_fixtures() -> dict[str, Fixture]:
    """The default fixture of every operation (variants are loaded by name)."""
    fixtures: dict[str, Fixture] = {}
    for file in sorted(FIXTURES.glob("*.json")):
        if "--" in file.stem:
            continue
        fixture = _load(file)
        assert file.name == fixture_filename(fixture.operation), (
            f"{file.name} holds {fixture.operation}; name it {fixture_filename(fixture.operation)}"
        )
        fixtures[fixture.operation] = fixture
    return fixtures


def load_variant(operation: str, variant: str) -> Fixture:
    fixture = _load(FIXTURES / fixture_filename(operation, variant))
    assert fixture.operation == operation, (fixture.file, fixture.operation)
    return fixture


def all_fixture_files() -> list[Path]:
    return sorted(FIXTURES.glob("*.json"))


def _template_regex(template: str) -> re.Pattern[str]:
    out, last = "", 0
    for match in re.finditer(r"\{([^}]+)\}", template):
        out += re.escape(template[last : match.start()])
        out += ".+" if match.group(1).endswith(":path") else "[^/]+"
        last = match.end()
    return re.compile("^" + out + re.escape(template[last:]) + "$")


def lookup(fixtures: dict[str, Fixture], path: str) -> Any:
    """Resolve ``"GET /v1/projects#data.0.id"`` to that value in the fixture's body."""
    operation, _, pointer = path.partition("#")
    value = fixtures[operation].body
    for part in [p for p in pointer.split(".") if p]:
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


@dataclass
class Replay:
    """An httpx handler answering from the fixture library and logging each request."""

    fixtures: dict[str, Fixture]
    overrides: dict[str, Fixture] = field(default_factory=dict)
    log: list[str] = field(default_factory=list)
    used: list[str] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._library = {**self.fixtures, **self.overrides}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = urlsplit(str(request.url))
        query = sorted(parse_qsl(url.query, keep_blank_values=True))
        line = f"> {request.method} {url.path}"
        if query:
            line += "?" + "&".join(f"{k}={v}" for k, v in query)
        self.log.append(line)
        body = request.read()
        if body:
            self.log.append("  " + _describe_body(request.headers.get("content-type", ""), body))
        if url.hostname == STORAGE_HOST:
            if request.method == "PUT":
                return httpx.Response(200, headers={"etag": '"snapshot-etag"'})
            return httpx.Response(200, content=STORED_BYTES)
        operation = identify(request.method, url.path)
        fixture = self._library.get(operation) if operation else None
        if fixture is None:
            self.unmatched.append(operation or f"{request.method} {url.path} (not public)")
            return httpx.Response(
                501,
                json={"error": {"code": "not_recorded", "message": "no recorded response"}},
            )
        self.used.append(operation)
        return _respond(fixture)


@cache
def _public_routes() -> list[tuple[str, re.Pattern[str], str]]:
    """Every operation the CLI may call: documented, or on its allowlist.

    The command table (``beakr api``) holds the documented core only, while the
    commands snapshotted here include ones over unlisted routes, so requests are
    identified against the documented contract plus the allowlisted operations
    in the same command-table shape.
    """
    from beakr_cli.api_manifest import build_manifest
    from tests._engine import cli_reachable_contract

    spec = cli_reachable_contract()
    templates = {(entry["method"], entry["path"]) for entry in build_manifest(spec)["operations"]}
    return sorted(
        ((method, _template_regex(path), path) for method, path in templates),
        # The most literal template wins: `/v1/projects/deleted` is its own
        # operation, not `/v1/projects/{project_id}`.
        key=lambda route: (route[2].count("{"), -len(route[2])),
    )


def identify(method: str, path: str) -> str | None:
    """The public operation (``METHOD /template``) a concrete request is for."""
    for route_method, pattern, template in _public_routes():
        if route_method == method and pattern.match(path):
            return f"{method} {template}"
    return None


#: Request fields the client fills with a fresh random value on every call.
RANDOM_FIELDS = frozenset({"idempotency_key"})


def _stable(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: "<random>" if k in RANDOM_FIELDS else _stable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_stable(v) for v in value]
    return value


def _describe_body(content_type: str, body: bytes) -> str:
    if "json" in content_type:
        return json.dumps(_stable(json.loads(body)), sort_keys=True, ensure_ascii=False)
    if "multipart" in content_type:
        # Boundaries are random; name the parts instead.
        names = sorted(set(re.findall(rb'name="([^"]+)"', body)))
        return "multipart: " + ", ".join(n.decode() for n in names)
    return f"{content_type or 'body'}: {len(body)} bytes"


def _respond(fixture: Fixture) -> httpx.Response:
    headers = {"x-request-id": "req_snapshot"}
    if fixture.text is not None:
        headers["content-type"] = fixture.content_type or "text/plain"
        return httpx.Response(fixture.status, headers=headers, text=fixture.text)
    if fixture.body is None:
        return httpx.Response(fixture.status, headers=headers)
    return httpx.Response(fixture.status, headers=headers, json=fixture.body)
