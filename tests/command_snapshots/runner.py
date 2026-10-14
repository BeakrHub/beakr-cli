"""Run one snapshot case against the replayed API and render its snapshot text.

A case runs twice: once with ``--json`` (what a script reads) and once as a
person at a terminal (``is_piped`` false, a fixed 100-column console without
colour; text lines are not wrapped, tables are laid out at that width). The
snapshot records, for each run, the requests the command sent, its exit code,
stdout and stderr, so a change to any of them shows up as a diff.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest
import respx
from typer.testing import CliRunner

from tests.command_snapshots.replay import API, Fixture, Replay, load_variant, lookup

WIDTH = 100
SNAPSHOTS = Path(__file__).resolve().parent.parent / "snapshots"


@dataclass(frozen=True)
class SnapshotCase:
    """One command line and the renders it is snapshotted in.

    ``argv`` items may name a fixture value as ``@GET /v1/projects#data.0.id`` so a
    re-recorded fixture (new ids) keeps the case pointing at real rows.
    ``variants`` answers an operation with a named variant fixture
    (``<operation file>--<variant>.json``) instead of its default; ``files`` are
    written to the working directory first; ``stdin`` is piped in.
    """

    name: str
    argv: tuple[str, ...]
    modes: tuple[str, ...] = ("json", "human")
    exit_code: int = 0
    stdin: str | None = None
    variants: dict[str, str] = field(default_factory=dict)
    files: dict[str, str] = field(default_factory=dict)
    #: A CLI defect this command hits on a contract-valid response. The case
    #: still runs and must fail; the day it passes, the marker has to go.
    known_defect: str = ""

    @property
    def snapshot(self) -> Path:
        return SNAPSHOTS / f"{self.name}.txt"


@dataclass(frozen=True)
class Rendered:
    text: str
    unmatched: list[str]
    exit_codes: list[int]


def _resolve_argv(case: SnapshotCase, fixtures: dict[str, Fixture]) -> list[str]:
    return [
        str(lookup(fixtures, item[1:])) if item.startswith("@") and " /v1/" in item else item
        for item in case.argv
    ]


def _as_a_person(monkeypatch: pytest.MonkeyPatch) -> None:
    """Render as at a terminal, deterministically: fixed width, no colour."""
    from beakr_cli import output

    original = output.is_piped
    for name, module in list(sys.modules.items()):
        if name.startswith("beakr_cli") and getattr(module, "is_piped", None) is original:
            monkeypatch.setattr(module, "is_piped", lambda: False)


def _fixed_consoles(monkeypatch: pytest.MonkeyPatch) -> None:
    from beakr_cli import output

    for console in (output.console, output.err_console):
        monkeypatch.setattr(console, "_width", WIDTH)
        # Long lines (paths, URLs) stay whole, so a per-run temporary path can be
        # normalized and a wrap point never moves with the path's length.
        monkeypatch.setattr(console, "soft_wrap", True)
        monkeypatch.setattr(console, "_color_system", None)
        monkeypatch.setattr(console, "_force_terminal", False)


def render(
    case: SnapshotCase,
    fixtures: dict[str, Fixture],
    monkeypatch: pytest.MonkeyPatch,
    work: Path,
) -> Rendered:
    """The snapshot text for ``case``, every request no fixture answered, the exit codes."""
    from beakr_cli.main import internal_app as app

    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_snapshot")
    monkeypatch.setenv("BEAKR_NO_UPDATE_CHECK", "1")
    monkeypatch.setenv("COLUMNS", str(WIDTH))
    _fixed_consoles(monkeypatch)
    for relative, content in case.files.items():
        (work / relative).parent.mkdir(parents=True, exist_ok=True)
        (work / relative).write_text(content)
    overrides = {op: load_variant(op, variant) for op, variant in case.variants.items()}
    argv = _resolve_argv(case, fixtures)
    sections: list[str] = []
    unmatched: list[str] = []
    exits: list[int] = []
    for mode in case.modes:
        args = [*argv, "--json"] if mode == "json" else list(argv)
        replay = Replay(fixtures, overrides)
        with monkeypatch.context() as scoped:
            if mode == "human":
                _as_a_person(scoped)
            with respx.mock(assert_all_called=False) as router:
                router.route().mock(side_effect=replay)
                result = CliRunner().invoke(app, args, input=case.stdin)
        unmatched += replay.unmatched
        if result.exception and not isinstance(result.exception, SystemExit):
            raise result.exception
        sections.append(
            "\n".join(
                [
                    f"$ beakr {' '.join(_shown(a) for a in args)}",
                    *replay.log,
                    f"[exit {result.exit_code}]",
                    "--- stdout",
                    _normalize(result.stdout, work).rstrip("\n"),
                    "--- stderr",
                    _normalize(result.stderr, work).rstrip("\n"),
                ]
            )
        )
        exits.append(result.exit_code)
    return Rendered("\n\n".join(sections) + "\n", unmatched, exits)


def _shown(arg: str) -> str:
    return f"'{arg}'" if (" " in arg or not arg) else arg


def _normalize(text: str, work: Path) -> str:
    # The per-test directory differs on every run (and is a symlinked path on macOS).
    root = work.parent
    for path in sorted({str(root.resolve()), str(root)}, key=len, reverse=True):
        text = text.replace(path, "<TMP>")
    return text
