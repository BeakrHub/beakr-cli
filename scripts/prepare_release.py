"""Prepare a release of this repository's package from the engine's public API contract.

Beakr's clients (the Python SDK ``beakr-sdk``, the TypeScript SDK ``@beakr/sdk``
and the CLI ``beakr-cli``) live in separate repositories, and each is generated
from or checked against the engine's public contract
(``docs/api/public-openapi.json``), so what changed in the API since the
package's last release decides both its version bump and its release notes.
This script is identical in every client repository; ``release/config.json``
names the package and the files that carry its version.

- **Version.** The contract at the last release (``release/released.json``
  records the engine commit it was built from) is compared with the current
  one using the engine's own compatibility rules (``contract_compat.py``,
  vendored). A breaking change needs a major bump, an added or changed
  operation or schema a minor bump, anything else (wording only) a patch.
  Below 1.0, semver puts breaking changes in the minor version, so both
  breaking and additive changes need a minor bump there.
- **Notes.** The API changelog (``docs/api/CHANGELOG.md``, written for API
  users) bullets that are new since that commit become this release's notes,
  grouped by the changelog's own sections, under one heading in
  ``CHANGELOG.md`` that names the package and its version.

Versions kept in step: every file ``release/config.json`` lists under
``version_files`` (for example ``pyproject.toml``, the module's
``__version__`` and ``uv.lock``; or ``package.json``, ``package-lock.json`` and
``src/version.ts``). ``release/config.json`` example::

    {
      "package": "beakr-sdk",
      "changelog_intro": "Releases of `beakr-sdk`, the Beakr Python SDK.",
      "version_files": [["pyproject.toml", "(?m)^version = \"([^\"]+)\""]]
    }

Commands (ENGINE is an engine checkout, default ``$BEAKR_ENGINE_PATH`` then a
sibling ``beakr-engine``, then the committed
snapshot ``snapshots/beakr-engine``; run them with
``uv run python`` or plain ``python3``, the script needs only the standard
library):

    uv run python scripts/prepare_release.py plan  [--engine ENGINE]
    uv run python scripts/prepare_release.py apply [--engine ENGINE] [--date YYYY-MM-DD]
    uv run python scripts/prepare_release.py check [--engine ENGINE]
    uv run python scripts/prepare_release.py record --engine-commit SHA

``check`` is what CI and the publish workflow run: it fails when versions are
out of step, when a version is lower than the contract changes require, or
when a version that differs from the last release has no release notes
covering every new API changelog entry. ``record`` updates
``release/released.json`` (the released version and the engine commit it was
built against) after a release is published. Nothing here builds,
tags or publishes anything.

``--base-contract`` / ``--base-changelog`` replace the git lookup of the last
release's files (tests, or an engine checkout without history).

ENGINE may also be a client repository's committed engine snapshot
(``snapshots/beakr-engine``, written by ``scripts/refresh_snapshots.py``): it
holds the last release's files under ``release-base/<commit>/`` and, in place
of the full internal contract, ``docs/api/still-served-openapi.json``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from contract_compat import StillServed, breaking_changes  # noqa: E402

CONTRACT = Path("docs/api/public-openapi.json")
#: Every operation a credential can reach (documented plus unlisted), written by
#: the engine's ``scripts.export_public_openapi --internal``.
REACHABLE_CONTRACT = Path("docs/api/internal-openapi.json")
API_CHANGELOG = Path("docs/api/CHANGELOG.md")
CHANGELOG = Path("CHANGELOG.md")
#: In an engine snapshot: the operations the last release documented that the
#: engine still serves, standing in for ``REACHABLE_CONTRACT``.
STILL_SERVED_CONTRACT = Path("docs/api/still-served-openapi.json")
#: In an engine snapshot: ``<commit>/<path>`` is ``path`` at that engine commit.
RELEASE_BASE = Path("release-base")

#: Contract keys that only carry prose: changing them alone is a patch.
_TEXT_KEYS = frozenset({"description", "summary", "title", "example", "examples"})
_SECTION = re.compile(r"^## (?P<title>.+)$")
_SUBSECTION = re.compile(r"^### (?P<title>.+)$")
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_CHANGELOG_TAIL = (
    "Each release lists the public API changes it supports, taken from the API changelog.\n"
)


# -- versions --------------------------------------------------------------------


@dataclass(frozen=True)
class Version:
    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, text: str) -> Version:
        match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", text.strip())
        if match is None:
            raise ValueError(f"not a release version: {text!r}")
        return cls(*(int(part) for part in match.groups()))

    def bumped(self, level: str) -> Version:
        if level == "major":
            if self.major == 0:
                # Below 1.0 semver carries breaking changes in the minor version.
                return Version(0, self.minor + 1, 0)
            return Version(self.major + 1, 0, 0)
        if level == "minor":
            return Version(self.major, self.minor + 1, 0)
        if level == "patch":
            return Version(self.major, self.minor, self.patch + 1)
        return self

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    def __lt__(self, other: Version) -> bool:
        return (self.major, self.minor, self.patch) < (other.major, other.minor, other.patch)


@dataclass(frozen=True)
class PackageConfig:
    """``release/config.json``: the package this repository releases."""

    package: str
    changelog_intro: str
    #: (file relative to the repo root, regex whose group 1 is the version).
    version_files: tuple[tuple[str, str], ...]
    #: The hand-written subsection ``apply`` carries over (see ``client_notes``).
    client_notes_title: str = "Client changes"

    @property
    def changelog_header(self) -> str:
        return f"# Changelog\n\n{self.changelog_intro.rstrip()}\n{_CHANGELOG_TAIL}"


def load_config(root: Path = ROOT) -> PackageConfig:
    data = json.loads((root / "release" / "config.json").read_text())
    return PackageConfig(
        package=data["package"],
        changelog_intro=data["changelog_intro"],
        version_files=tuple((name, pattern) for name, pattern in data["version_files"]),
        client_notes_title=data.get("client_notes_title", "Client changes"),
    )


def read_versions(root: Path, files: tuple[tuple[str, str], ...]) -> dict[str, str]:
    found: dict[str, str] = {}
    for index, (name, pattern) in enumerate(files):
        path = root / name
        if not path.exists():
            continue
        match = re.search(pattern, path.read_text())
        if match:
            found[f"{name}#{index}"] = match.group(1)
    return found


def write_version(root: Path, files: tuple[tuple[str, str], ...], version: str) -> None:
    for name, pattern in files:
        path = root / name
        if not path.exists():
            continue
        text = path.read_text()
        match = re.search(pattern, text)
        if match is None:
            continue
        start, end = match.span(1)
        path.write_text(text[:start] + version + text[end:])


# -- the API's changes ----------------------------------------------------------------


def _without_text(node: Any) -> Any:
    if isinstance(node, dict):
        return {k: _without_text(v) for k, v in node.items() if k not in _TEXT_KEYS}
    if isinstance(node, list):
        return [_without_text(item) for item in node]
    return node


def still_served_by(reachable: dict[str, Any] | None) -> StillServed | None:
    """Whether an operation the contract stopped documenting is still served.

    The engine's compatibility gate passes the same rule
    (``tests/contract/test_public_api_compat.py::_still_public``): an operation
    that left the documented contract but is still reachable under the same
    scope is unlisted, not removed. Here "reachable" is the engine's full
    contract (``docs/api/internal-openapi.json``). Without it, every missing
    operation counts as removed.
    """
    if reachable is None:
        return None
    scopes = {
        (method.upper(), path): operation.get("x-beakr-scope")
        for path, item in reachable.get("paths", {}).items()
        for method, operation in item.items()
        if isinstance(operation, dict)
    }

    def served(method: str, path: str, scope: str | None) -> bool:
        key = (method.upper(), path)
        return key in scopes and scopes[key] == scope

    return served


def required_bump(
    base: dict[str, Any] | None,
    current: dict[str, Any],
    reachable: dict[str, Any] | None = None,
) -> tuple[str, list[str]]:
    """The bump the contract change needs, with the breaking changes found.

    ``initial`` when there is no released contract yet (any version is fine).
    ``reachable`` (the engine's full contract) lets an operation that is only
    unlisted, not removed, count as a non-breaking change.
    """
    if base is None:
        return "initial", []
    breaks = breaking_changes(base, current, still_served_by(reachable))
    if breaks:
        return "major", breaks
    if _without_text(base) != _without_text(current):
        return "minor", []
    if base != current:
        return "patch", []
    return "none", []


@dataclass
class ChangelogSection:
    title: str
    bullets: list[str] = field(default_factory=list)


def changelog_bullets(text: str) -> list[ChangelogSection]:
    """Top-level bullets of the API changelog, grouped by their ``###`` section."""
    sections: list[ChangelogSection] = []
    current: ChangelogSection | None = None
    bullet: list[str] | None = None

    def close_bullet() -> None:
        nonlocal bullet
        if bullet is not None and current is not None:
            current.bullets.append("\n".join(line.rstrip() for line in bullet).rstrip())
        bullet = None

    for line in _COMMENT.sub("", text).splitlines():
        if _SECTION.match(line) or _SUBSECTION.match(line):
            close_bullet()
            heading = _SUBSECTION.match(line)
            current = ChangelogSection(heading.group("title").strip() if heading else "Changes")
            sections.append(current)
        elif line.startswith("- "):
            close_bullet()
            bullet = [line]
        elif bullet is not None and (line.startswith("  ") or not line.strip()):
            bullet.append(line)
        else:
            close_bullet()
    close_bullet()
    return [section for section in sections if section.bullets]


def new_bullets(base_text: str | None, current_text: str) -> list[ChangelogSection]:
    """Bullets of the current API changelog that the released one did not have."""
    seen = set()
    if base_text is not None:
        seen = {b for section in changelog_bullets(base_text) for b in section.bullets}
    fresh: list[ChangelogSection] = []
    for section in changelog_bullets(current_text):
        bullets = [b for b in section.bullets if b not in seen]
        if bullets:
            fresh.append(ChangelogSection(section.title, bullets))
    return fresh


def release_heading(package: str, version: str) -> str:
    return f"{package} {version}"


def render_release(heading: str, day: str, sections: list[ChangelogSection]) -> str:
    lines = [f"## {heading} ({day})", ""]
    if not sections:
        lines += ["No public API changes.", ""]
    for section in sections:
        lines += [f"### {section.title}", ""]
        lines += [bullet for bullet in section.bullets]
        lines.append("")
    return "\n".join(lines)


def client_notes(release_body: str, title: str = "Client changes") -> str:
    """The hand-written ``### <title>`` subsection of one release's notes, or ``""``.

    The one hand-written part of a release's notes: changes to the client
    itself (methods, flags, defaults, retries) that no API changelog entry
    describes. ``apply`` regenerates everything else from the API changelog and
    carries this subsection over verbatim; ``release/config.json``'s
    ``client_notes_title`` names it.
    """
    marker = f"### {title}\n"
    start = release_body.find(marker)
    if start == -1:
        return ""
    end = release_body.find("\n### ", start + len(marker))
    block = release_body[start:] if end == -1 else release_body[start : end + 1]
    return block.rstrip("\n") + "\n"


def notes_cover(changelog: str, heading: str, sections: list[ChangelogSection]) -> list[str]:
    """API changelog bullets missing from the release notes under ``heading``."""
    start = changelog.find(f"## {heading} (")
    if start == -1:
        return ["(no release notes for this version)"]
    rest = changelog[start + 3 :]
    end = rest.find("\n## ")
    body = rest if end == -1 else rest[:end]
    return [b.splitlines()[0] for s in sections for b in s.bullets if b not in body]


# -- inputs -----------------------------------------------------------------------


def engine_path(argument: str | None) -> Path:
    candidates = [argument, os.environ.get("BEAKR_ENGINE_PATH")]
    candidates += [
        str(ROOT.parent / "beakr-engine"),
        # The committed snapshot (scripts/refresh_snapshots.py), what CI reads.
        str(ROOT / "snapshots" / "beakr-engine"),
    ]
    for candidate in candidates:
        if candidate and (Path(candidate) / CONTRACT).exists():
            return Path(candidate)
    raise SystemExit("No engine checkout with docs/api/public-openapi.json found (--engine).")


def released(root: Path = ROOT) -> dict[str, Any]:
    return json.loads((root / "release" / "released.json").read_text())


def _at_commit(engine: Path, commit: str | None, path: Path) -> str | None:
    if not commit:
        return None
    snapshotted = engine / RELEASE_BASE / commit / path
    if snapshotted.exists():
        return snapshotted.read_text()
    result = subprocess.run(
        ["git", "-C", str(engine), "show", f"{commit}:{path.as_posix()}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(
            f"Cannot read {path} at the last release's engine commit {commit}: "
            f"{result.stderr.strip()} (fetch the engine's history, or pass --base-contract "
            "and --base-changelog)."
        )
    return result.stdout


@dataclass
class Plan:
    package: str
    bump: str
    breaking: list[str]
    sections: list[ChangelogSection]
    current: str
    released: str | None
    required: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "package": self.package,
            "bump": self.bump,
            "breaking_changes": self.breaking,
            "new_api_changelog_entries": sum(len(s.bullets) for s in self.sections),
            "current": self.current,
            "released": self.released,
            "required_at_least": self.required,
        }


def _required(released_version: str | None, bump: str) -> str | None:
    if released_version is None or bump == "initial":
        return None
    if bump == "none":
        return released_version
    return str(Version.parse(released_version).bumped(bump))


def _single(versions: dict[str, str], label: str) -> str | None:
    values = set(versions.values())
    if len(values) > 1:
        raise SystemExit(f"{label} versions are out of step: {versions}")
    return values.pop() if values else None


def make_plan(args: argparse.Namespace, root: Path = ROOT) -> Plan:
    config = load_config(root)
    engine = engine_path(args.engine)
    record = released(root)
    current_contract = json.loads((engine / CONTRACT).read_text())
    reachable_path = engine / REACHABLE_CONTRACT
    if not reachable_path.exists():
        reachable_path = engine / STILL_SERVED_CONTRACT
    reachable = json.loads(reachable_path.read_text()) if reachable_path.exists() else None
    base_contract_text = (
        Path(args.base_contract).read_text()
        if args.base_contract
        else _at_commit(engine, record.get("engine_commit"), CONTRACT)
    )
    base_changelog = (
        Path(args.base_changelog).read_text()
        if args.base_changelog
        else _at_commit(engine, record.get("engine_commit"), API_CHANGELOG)
    )
    bump, breaking = required_bump(
        json.loads(base_contract_text) if base_contract_text else None,
        current_contract,
        reachable,
    )
    sections = new_bullets(base_changelog, (engine / API_CHANGELOG).read_text())
    current = _single(read_versions(root, config.version_files), config.package)
    if current is None:
        raise SystemExit(f"No {config.package} version found in release/config.json's files.")
    return Plan(
        package=config.package,
        bump=bump,
        breaking=breaking,
        sections=sections,
        current=current,
        released=record.get("version"),
        required=_required(record.get("version"), bump),
    )


# -- commands ---------------------------------------------------------------------


def _next(current: str, required: str | None) -> str:
    if required is None or not Version.parse(current) < Version.parse(required):
        return current
    return required


def cmd_apply(plan: Plan, day: str, root: Path = ROOT) -> str:
    config = load_config(root)
    version = _next(plan.current, plan.required)
    write_version(root, config.version_files, version)
    heading = release_heading(plan.package, version)
    header = config.changelog_header
    changelog_path = root / CHANGELOG
    existing = changelog_path.read_text() if changelog_path.exists() else header
    body = existing[len(header) :] if existing.startswith(header) else existing
    start = body.find(f"## {heading} (")
    kept = ""
    if start != -1:
        # Regenerate this pending release's notes (the API changelog may have
        # grown since the last apply); earlier releases are left as written,
        # and this release's hand-written client notes are carried over.
        end = body.find("\n## ", start + 3)
        kept = client_notes(
            body[start : end + 1 if end != -1 else len(body)], config.client_notes_title
        )
        body = body[:start] + (body[end + 1 :] if end != -1 else "")
    notes = render_release(heading, day, plan.sections)
    if kept:
        notes = notes.rstrip("\n") + "\n\n" + kept + "\n"
    changelog_path.write_text((header + "\n" + notes + body.lstrip("\n")).rstrip("\n") + "\n")
    return heading


def cmd_check(plan: Plan, root: Path = ROOT) -> list[str]:
    problems: list[str] = []
    if plan.required and Version.parse(plan.current) < Version.parse(plan.required):
        problems.append(
            f"{plan.package} {plan.current} is below {plan.required}, which the {plan.bump} "
            "API change since the last release requires (run scripts/prepare_release.py apply)."
        )
    if plan.current != plan.released:
        heading = release_heading(plan.package, plan.current)
        notes_path = root / CHANGELOG
        changelog = notes_path.read_text() if notes_path.exists() else ""
        missing = notes_cover(changelog, heading, plan.sections)
        if missing:
            problems.append(
                f"CHANGELOG.md has no complete notes for {heading}; missing: "
                + "; ".join(missing[:5])
                + (f" (and {len(missing) - 5} more)" if len(missing) > 5 else "")
            )
    return problems


def cmd_record(commit: str, root: Path = ROOT) -> dict[str, Any]:
    config = load_config(root)
    version = _single(read_versions(root, config.version_files), config.package)
    record = {"engine_commit": commit, "version": version}
    (root / "release" / "released.json").write_text(json.dumps(record, indent=2) + "\n")
    return record


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["plan", "apply", "check", "record"])
    parser.add_argument("--engine")
    parser.add_argument("--base-contract")
    parser.add_argument("--base-changelog")
    parser.add_argument("--date", default=None)
    parser.add_argument("--engine-commit")
    args = parser.parse_args(argv)

    if args.command == "record":
        if not args.engine_commit:
            parser.error("record needs --engine-commit")
        print(json.dumps(cmd_record(args.engine_commit), indent=2))
        return 0
    plan = make_plan(args)
    if args.command == "plan":
        print(json.dumps(plan.as_dict(), indent=2))
        return 0
    if args.command == "apply":
        heading = cmd_apply(plan, args.date or date.today().isoformat())
        print(f"prepared {heading}")
        return 0
    problems = cmd_check(plan)
    for problem in problems:
        print(f"FAIL: {problem}")
    if not problems:
        print(f"release versions and notes are consistent ({plan.bump} API change)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
