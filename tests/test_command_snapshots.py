"""Snapshot tests of the hand-written commands against recorded API responses (W7 item 7).

Root cause this guards: the hand-written commands (``ask``, ``kb cat``,
``context``, ``projects``, ``runs`` ...) were tested only with per-test respx
mocks of hand-picked fields, asserting fragments of the output. A renderer that
read a field the contract does not have, mis-shaped its ``--json``, or crashed on
a contract-valid response passed those tests (the first run of this suite found
``kb links`` and ``kb sources`` crashing on every real answer).

Here every command runs against one library of recorded responses, one per
public operation (``tests/fixtures/recorded_api``; see
``tests/command_snapshots/replay.py``), each validated against the operation's
response schema in the public contract. Each command is run with ``--json`` and
as a person at a terminal, and the requests it sent, its exit code, stdout and
stderr are compared with a golden file in ``tests/snapshots``.

To accept an intended change, rewrite the golden files and review the diff::

    uv run pytest tests/test_command_snapshots.py --update-snapshots
    # or: BEAKR_UPDATE_SNAPSHOTS=1 uv run pytest tests/test_command_snapshots.py

To refresh the responses from the real engine, run beakr-engine's positive-path
suite with ``BEAKR_RECORD_FIXTURES`` pointing at ``tests/fixtures/recorded_api``
(it rewrites each operation's file in place), then update the snapshots.
"""

from __future__ import annotations

import json
import os

import click
import pytest
import typer
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from beakr_cli.main import internal_app as app
from tests._engine import cli_reachable_contract
from tests.command_snapshots.cases import CASES, EXEMPT
from tests.command_snapshots.replay import (
    FIXTURES,
    _load,
    all_fixture_files,
    fixture_filename,
    load_fixtures,
)
from tests.command_snapshots.runner import SNAPSHOTS, render


def _updating(config: pytest.Config) -> bool:
    return bool(config.getoption("--update-snapshots")) or (
        os.environ.get("BEAKR_UPDATE_SNAPSHOTS") == "1"
    )


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
def test_command_output_matches_its_snapshot(case, monkeypatch, tmp_path, pytestconfig):
    fixtures = load_fixtures()
    if case.known_defect:
        # The command crashes on a contract-valid response. When it stops
        # crashing this raises "DID NOT RAISE": drop the marker, then record the
        # snapshot with --update-snapshots.
        with pytest.raises(Exception):  # noqa: B017 - any crash is the recorded defect
            render(case, fixtures, monkeypatch, tmp_path / "work")
        pytest.xfail(case.known_defect)
    _check(case, fixtures, monkeypatch, tmp_path, update=_updating(pytestconfig))


def _check(case, fixtures, monkeypatch, tmp_path, *, update: bool) -> None:
    rendered = render(case, fixtures, monkeypatch, tmp_path / "work")
    assert rendered.unmatched == [], (
        f"{case.name} called operations with no recorded response: {rendered.unmatched}; "
        f"add tests/fixtures/recorded_api/<operation>.json for each"
    )
    assert rendered.exit_codes == [case.exit_code] * len(case.modes), rendered.text
    if update:
        case.snapshot.write_text(rendered.text)
        return
    assert case.snapshot.exists(), (
        f"no snapshot for {case.name}; run with --update-snapshots and review {case.snapshot}"
    )
    assert rendered.text == case.snapshot.read_text(), (
        f"{case.name} output changed; if intended, rerun with --update-snapshots "
        "and review the diff"
    )


def test_cases_have_unique_names_and_every_snapshot_has_a_case():
    names = [case.name for case in CASES]
    assert len(names) == len(set(names))
    orphans = sorted(p.stem for p in SNAPSHOTS.glob("*.txt") if p.stem not in set(names))
    assert orphans == [], f"snapshots with no case (delete them): {orphans}"


# --- every hand-written command is covered -------------------------------------


def _visible_hand_written_commands() -> set[str]:
    found: set[str] = set()

    def walk(command: click.Command, path: list[str]) -> None:
        if getattr(command, "hidden", False):
            return
        if isinstance(command, click.Group):
            for name, sub in command.commands.items():
                walk(sub, [*path, name])
        else:
            found.add(" ".join(path))

    # `beakr api` is generated and lazily built, so it is not in `commands`:
    # beakr-engine's tests/api/test_cli_web_action_fidelity.py covers it.
    walk(typer.main.get_command(app), [])
    return found


def _covered(command: str) -> bool:
    words = command.split()
    return any(list(case.argv[: len(words)]) == words for case in CASES)


def test_every_hand_written_command_is_snapshotted_or_exempt():
    missing = sorted(
        command
        for command in _visible_hand_written_commands()
        if not _covered(command) and command not in EXEMPT
    )
    assert missing == [], f"commands with no snapshot case and no exemption: {missing}"


def test_exemptions_name_real_commands_without_a_case():
    commands = _visible_hand_written_commands()
    for command, reason in EXEMPT.items():
        assert command in commands, f"exempt command {command!r} no longer exists"
        assert not _covered(command), f"{command!r} has a case; drop its exemption"
        assert reason.strip()


# --- every recorded response is what the contract documents ---------------------


@pytest.fixture(scope="module")
def contract() -> dict:
    return cli_reachable_contract()


def _validator(contract: dict, schema: dict) -> Draft202012Validator:
    rewritten = json.loads(
        json.dumps(contract).replace('"#/components/', '"urn:beakr-spec#/components/')
    )
    registry = Registry().with_resource(
        "urn:beakr-spec", Resource.from_contents(rewritten, default_specification=DRAFT202012)
    )
    schema = json.loads(
        json.dumps(schema).replace('"#/components/', '"urn:beakr-spec#/components/')
    )
    return Draft202012Validator(schema, registry=registry)


@pytest.mark.parametrize("file", all_fixture_files(), ids=lambda p: p.name)
def test_recorded_response_matches_the_contract(file, contract):
    fixture = _load(file)
    assert file.name == fixture_filename(fixture.operation, file.stem.partition("--")[2] or None)
    assert fixture.source, f"{file.name}: say where the response came from in 'source'"
    method, path = fixture.method.lower(), fixture.template
    operation = contract["paths"].get(path, {}).get(method)
    assert operation is not None, f"{fixture.operation} is not a public operation"
    response = operation["responses"].get(str(fixture.status))
    assert response is not None and str(fixture.status).startswith("2"), (
        f"{fixture.operation}: {fixture.status} is not a documented success status"
    )
    content = response.get("content") or {}
    if fixture.body is None and fixture.text is None:
        assert not content or fixture.status == 204, f"{fixture.operation} documents a body"
        return
    if fixture.text is not None:
        assert fixture.content_type in content, (fixture.operation, list(content))
        return
    media = next((m for m in content if "json" in m), None)
    assert media is not None, f"{fixture.operation} documents no JSON body"
    schema = content[media].get("schema") or {}
    errors = sorted(
        _validator(contract, schema).iter_errors(fixture.body),
        key=lambda error: list(error.absolute_path),
    )
    assert not errors, [
        f"{'/'.join(map(str, e.absolute_path))}: {e.message[:200]}" for e in errors[:5]
    ]


def test_the_fixture_library_is_not_empty():
    assert FIXTURES.is_dir() and all_fixture_files()
