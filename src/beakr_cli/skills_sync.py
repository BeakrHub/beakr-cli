"""Two-way sync between Beakr skills and local agent skill dirs.

The caller's library syncs here. Syncing project, organization and integration
skills (where every local edit became a proposal for the skill's owners) is
not in this release; a folder an earlier version pulled from one of those skills
is never uploaded into the library.

Local layout is the agentskills.io folder Claude Code and Codex both read:
``<claude home>/skills/<slug>/SKILL.md`` and ``<codex home>/skills/<slug>/SKILL.md``.

Conflicts are detected against ``~/.beakr/skills_sync.json``, which records,
per slug, the remote ``updated_at`` and a hash of each local copy as of the last
sync. A side "changed" when it differs from that record; when both sides
changed, the skill is reported as a conflict and left alone unless ``force``.
The bundled ``beakr`` skill is installed by ``beakr install`` and never synced.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from beakr_sdk.errors import ConflictError

from beakr_cli._private_api import PrivateBeakr as Beakr
from beakr_cli._private_api.resources.skills import bundle_sha256
from beakr_cli.commands.install import _claude_home, _codex_home
from beakr_cli.hook_runtime import read_state, write_state

STATE_FILE = "skills_sync.json"
SKILL_MD = "SKILL.md"
#: Installed by ``beakr install``; syncing it would fight that installer.
RESERVED_SLUGS = frozenset({"beakr"})
TARGETS = ("claude", "codex")
#: Scopes and the overlay marker earlier versions recorded for folders pulled
#: from shared skills; such a folder is never pushed into the personal library.
_SHARED_SCOPES = ("project", "org", "integration")
_OVERLAY_SUFFIX = ".overlay"


@dataclass
class SyncAction:
    slug: str
    target: str
    # pulled | pushed-new | pushed | unchanged | conflict | skipped | local-changes | refused
    action: str
    detail: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "slug": self.slug,
            "target": self.target,
            "action": self.action,
            "detail": self.detail,
        }


@dataclass
class SyncPlan:
    dry_run: bool = False
    force: bool = False
    actions: list[SyncAction] = field(default_factory=list)


def target_root(target: str) -> Path:
    if target == "claude":
        return _claude_home() / "skills"
    if target == "codex":
        return _codex_home() / "skills"
    raise ValueError(f"Unknown target '{target}'")


class UnsafeSkillFolder(ValueError):
    """A skill folder name that would leave the target's skills directory."""


def skill_dir(target: str, folder: str) -> Path:
    """The folder for one skill under a target's skills directory, contained.

    Folder names come from the server (slugs, overlay targets), so they are
    checked before anything is read, written or deleted: one plain path
    component, and its resolved location (symlinks followed) must sit directly
    in the resolved skills directory. Nothing is rewritten into a different
    name; an unsafe one is refused (audit C047).
    """
    root = target_root(target)
    if (
        not folder
        or folder in {".", ".."}
        or "/" in folder
        or "\\" in folder
        or "\x00" in folder
        or Path(folder).is_absolute()
        or Path(folder).name != folder
    ):
        raise UnsafeSkillFolder(f"refusing unsafe skill folder name {folder!r}")
    directory = root / folder
    if directory.resolve().parent != root.resolve():
        raise UnsafeSkillFolder(f"skill folder {folder!r} resolves outside {root}")
    return directory


def resolve_targets(target: str) -> list[str]:
    return list(TARGETS) if target == "both" else [target]


def parse_frontmatter(text: str) -> dict[str, str]:
    """``key: value`` pairs from a leading ``---`` block (enough for name/description)."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    fields: dict[str, str] = {}
    for line in text[3:end].splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() and not key.startswith((" ", "\t")):
            fields[key.strip()] = value.strip().strip("'\"")
    return fields


def linked_entries(directory: Path) -> list[str]:
    """Paths inside a skill folder that are symlinks or resolve outside it.

    A skill folder is uploaded as-is, so a link inside it (``references/x`` ->
    ``~/.ssh/config``) would carry outside bytes to Beakr, and a pull would write
    through it. Sync refuses a folder that holds any (audit C078); the walk never
    follows a linked directory.
    """
    root = directory.resolve()
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(directory, followlinks=False):
        base = Path(dirpath)
        for name in [*dirnames, *filenames]:
            path = base / name
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                found.append(path.relative_to(directory).as_posix())
    return sorted(found)


def _read_text_no_follow(path: Path) -> str:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as handle:
        return handle.read().decode("utf-8")


def read_local_files(directory: Path) -> dict[str, str]:
    """Every text file in a skill folder, keyed by relative POSIX path.

    Symlinks (files or directories) are never followed or read, and a file is
    opened without following a final link, so only bytes that live inside the
    skill folder can reach an upload (audit C078). Callers that upload refuse a
    folder with links outright (``linked_entries``) rather than sending a partial
    skill.
    """
    root = directory.resolve()
    files: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(directory, followlinks=False):
        base = Path(dirpath)
        dirnames[:] = sorted(
            d for d in dirnames if not d.startswith(".") and not (base / d).is_symlink()
        )
        for name in sorted(filenames):
            path = base / name
            relative = path.relative_to(directory)
            if name.startswith(".") or path.is_symlink() or not path.is_file():
                continue
            if not path.resolve().is_relative_to(root):
                continue
            try:
                files[relative.as_posix()] = _read_text_no_follow(path)
            except (UnicodeDecodeError, OSError):
                # The bundle API carries text; a binary asset cannot round-trip.
                continue
    return dict(sorted(files.items()))


def hash_files(files: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for name in sorted(files):
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(files[name].encode())
        digest.update(b"\0")
    return digest.hexdigest()


def write_local_files(directory: Path, files: dict[str, str]) -> None:
    """Mirror ``files`` into ``directory``, removing files the bundle no longer has.

    Refuses (``UnsafeSkillFolder``) before writing anything when the folder holds
    a link, so a pull can never write through one to a file outside it (C078).
    """
    if directory.exists():
        links = linked_entries(directory)
        if links:
            raise UnsafeSkillFolder(
                f"skill folder {directory.name!r} contains links ({', '.join(links)}); "
                "remove them, then sync again"
            )
    directory.mkdir(parents=True, exist_ok=True)
    for stale in set(read_local_files(directory)) - set(files):
        (directory / stale).unlink()
    for name, content in files.items():
        path = directory / name
        # A bundle path must stay inside the skill folder.
        if not path.resolve().is_relative_to(directory.resolve()):
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def local_skill_dirs(target: str) -> list[Path]:
    root = target_root(target)
    if not root.is_dir():
        return []
    return [
        child
        for child in sorted(root.iterdir())
        if child.is_dir()
        and not child.name.startswith(".")
        and child.name not in RESERVED_SLUGS
        and (child / SKILL_MD).is_file()
    ]


class SkillsSync:
    """One sync run; state is loaded at start and saved after each change."""

    def __init__(
        self,
        sdk: Beakr,
        *,
        dry_run: bool = False,
        force: bool = False,
    ) -> None:
        self._sdk = sdk
        self.plan = SyncPlan(dry_run=dry_run, force=force)
        self._state: dict[str, Any] = read_state(STATE_FILE).get("skills", {})
        self._library: dict[str, dict[str, Any]] | None = None

    def library(self, *, refresh: bool = False) -> dict[str, dict[str, Any]]:
        if self._library is None or refresh:
            self._library = {
                str(skill["slug"]): skill
                for skill in self._sdk.skills.list()
                if skill.get("slug") and skill["slug"] not in RESERVED_SLUGS
            }
        return self._library

    def _entry(self, slug: str) -> dict[str, Any]:
        return self._state.setdefault(slug, {"local_sha256": {}})

    def _save(self) -> None:
        if not self.plan.dry_run:
            write_state(STATE_FILE, {"skills": self._state})

    def _record(self, action: SyncAction) -> None:
        self.plan.actions.append(action)

    # -- pull ----------------------------------------------------------------

    def pull(self, targets: Iterable[str]) -> None:
        targets = list(targets)
        for slug, skill in self.library().items():
            for target in targets:
                self._pull_one(slug, skill, target)

    def _pull_one(self, slug: str, skill: dict[str, Any], target: str) -> None:
        try:
            directory = skill_dir(target, slug)
        except UnsafeSkillFolder as exc:
            self._record(SyncAction(slug, target, "refused", str(exc)))
            return
        entry = self._entry(slug)
        remote_updated = str(skill.get("updated_at") or "")
        recorded_local = entry["local_sha256"].get(target)
        local_hash = hash_files(read_local_files(directory)) if directory.exists() else None
        remote_changed = entry.get("remote_updated_at") != remote_updated
        local_changed = local_hash is not None and local_hash != recorded_local

        if local_hash is not None and not remote_changed and not local_changed:
            self._record(SyncAction(slug, target, "unchanged"))
            return
        files = self._sdk.skills.bundle(str(skill["id"])).get("files") or {}
        remote_hash = hash_files(files)
        remote_bundle = bundle_sha256(files)
        if local_hash == remote_hash:
            self._remember(slug, skill, target, local_hash, remote_updated, remote_bundle)
            self._record(SyncAction(slug, target, "unchanged"))
            return
        if local_changed and not self.plan.force:
            if remote_changed:
                self._record(
                    SyncAction(
                        slug,
                        target,
                        "conflict",
                        "changed locally and in Beakr; --force takes Beakr's",
                    )
                )
            else:
                self._record(SyncAction(slug, target, "local-changes", "push to upload"))
            return
        if not self.plan.dry_run:
            try:
                write_local_files(directory, files)
            except UnsafeSkillFolder as exc:
                self._record(SyncAction(slug, target, "refused", str(exc)))
                return
        self._remember(slug, skill, target, remote_hash, remote_updated, remote_bundle)
        self._record(SyncAction(slug, target, "pulled", str(directory)))

    def _remember(
        self,
        slug: str,
        skill: dict[str, Any],
        target: str,
        local_hash: str,
        remote_updated: str,
        remote_bundle: str | None = None,
    ) -> None:
        entry = self._entry(slug)
        entry["skill_id"] = str(skill["id"])
        entry["remote_updated_at"] = remote_updated
        if remote_bundle is not None:
            # The server's digest of the bundle as synced: the base a later push
            # sends as expected_sha256 (review R027).
            entry["remote_bundle_sha256"] = remote_bundle
        entry["local_sha256"][target] = local_hash
        self._save()

    # -- push ----------------------------------------------------------------

    def push(self, targets: Iterable[str]) -> None:
        pushed_hashes: dict[str, str] = {}
        for target in targets:
            for directory in local_skill_dirs(target):
                try:
                    # A symlinked folder must not carry files from outside the
                    # skills directory up to Beakr (audit C047).
                    skill_dir(target, directory.name)
                except UnsafeSkillFolder as exc:
                    self._record(SyncAction(directory.name, target, "refused", str(exc)))
                    continue
                self._push_one(directory, target, pushed_hashes)
        if pushed_hashes and not self.plan.dry_run:
            # Record the server's new updated_at so the next pull does not see
            # our own upload as a remote change.
            library = self.library(refresh=True)
            for slug in pushed_hashes:
                if slug in library:
                    self._entry(slug)["remote_updated_at"] = str(
                        library[slug].get("updated_at") or ""
                    )
            self._save()

    def _push_one(self, directory: Path, target: str, pushed_hashes: dict[str, str]) -> None:
        slug = directory.name
        bound_scope = (self._state.get(slug) or {}).get("scope")
        if bound_scope in _SHARED_SCOPES or _OVERLAY_SUFFIX in slug:
            # A folder an earlier version pulled from a project, org or
            # integration skill (or a project overlay) belongs to that skill;
            # it is never uploaded into the personal library under its slug.
            scope = bound_scope or "project"
            self._record(
                SyncAction(
                    slug, target, "skipped", f"a {scope} skill; shared skill sync is parked"
                )
            )
            return
        links = linked_entries(directory)
        if links:
            self._record(
                SyncAction(
                    slug,
                    target,
                    "refused",
                    f"contains links ({', '.join(links)}); nothing was sent",
                )
            )
            return
        files = read_local_files(directory)
        meta = parse_frontmatter(files.get(SKILL_MD, ""))
        if not meta.get("name"):
            self._record(SyncAction(slug, target, "skipped", "SKILL.md has no frontmatter name"))
            return
        local_hash = hash_files(files)
        entry = self._state.get(slug)
        if slug in pushed_hashes:
            # The same skill under the other agent's directory.
            if pushed_hashes[slug] == local_hash:
                self._entry(slug)["local_sha256"][target] = local_hash
                self._record(SyncAction(slug, target, "unchanged", "already pushed"))
            else:
                self._record(
                    SyncAction(
                        slug,
                        target,
                        "conflict",
                        "differs from the copy pushed from the other target",
                    )
                )
            return
        remote = self.library().get(slug)

        if entry is None or not entry.get("skill_id"):
            if remote is not None and not self.plan.force:
                self._record(
                    SyncAction(
                        slug, target, "conflict", "exists in Beakr already; pull first or --force"
                    )
                )
                return
            if remote is not None:
                self._replace(slug, str(remote["id"]), files, target, local_hash, pushed_hashes)
                return
            if not self.plan.dry_run:
                created = self._sdk.skills.import_bundle(files, slug_hint=slug)
                new_entry = self._entry(slug)
                new_entry["skill_id"] = str(created["id"])
                new_entry["remote_updated_at"] = str(created.get("updated_at") or "")
                new_entry["local_sha256"][target] = local_hash
                self._save()
            pushed_hashes[slug] = local_hash
            self._record(SyncAction(slug, target, "pushed-new"))
            return

        if entry["local_sha256"].get(target) == local_hash:
            self._record(SyncAction(slug, target, "unchanged"))
            return
        remote_changed = remote is not None and str(remote.get("updated_at") or "") != entry.get(
            "remote_updated_at"
        )
        if remote_changed and not self.plan.force:
            self._record(
                SyncAction(
                    slug,
                    target,
                    "conflict",
                    "changed locally and in Beakr; use --force to upload yours",
                )
            )
            return
        self._replace(slug, str(entry["skill_id"]), files, target, local_hash, pushed_hashes)

    def _replace(
        self,
        slug: str,
        skill_id: str,
        files: dict[str, str],
        target: str,
        local_hash: str,
        pushed_hashes: dict[str, str],
    ) -> None:
        if not self.plan.dry_run:
            entry = self._entry(slug)
            # The write is conditional on the bundle still being the one this
            # folder was last synced with, so an edit made in Beakr since is never
            # overwritten silently; --force overwrites (review R027). An entry
            # synced before the base was recorded falls back to the bundle as it
            # stands now, which the updated_at check above already vetted.
            expected = entry.get("remote_bundle_sha256") or bundle_sha256(
                self._sdk.skills.bundle(skill_id).get("files") or {}
            )
            try:
                written = self._sdk.skills.replace_bundle(
                    skill_id, files, expected_sha256=expected, force=self.plan.force
                )
            except ConflictError:
                self._record(
                    SyncAction(
                        slug,
                        target,
                        "conflict",
                        "changed in Beakr since your last sync; pull first (your local "
                        "edits are kept) or --force to upload yours",
                    )
                )
                return
            written = written if isinstance(written, dict) else {}
            entry["remote_bundle_sha256"] = written.get("files_sha256") or bundle_sha256(
                written.get("files") or files
            )
            entry["skill_id"] = skill_id
            entry["local_sha256"][target] = local_hash
            self._save()
        pushed_hashes[slug] = local_hash
        self._record(SyncAction(slug, target, "pushed"))


def sync_skills(
    sdk: Beakr,
    *,
    direction: str = "both",
    target: str = "both",
    dry_run: bool = False,
    force: bool = False,
) -> list[SyncAction]:
    """Sync the caller's library. ``both`` pushes local changes first, then pulls remote ones."""
    runner = SkillsSync(sdk, dry_run=dry_run, force=force)
    targets = resolve_targets(target)
    if direction in ("push", "both"):
        runner.push(targets)
    if direction in ("pull", "both"):
        runner.pull(targets)
    return runner.plan.actions


def local_status(slug: str) -> dict[str, str]:
    """Per target: absent, synced, or modified (relative to the last sync)."""
    entry = read_state(STATE_FILE).get("skills", {}).get(slug) or {}
    recorded = entry.get("local_sha256") or {}
    status: dict[str, str] = {}
    for target in TARGETS:
        directory = skill_dir(target, slug)
        if not directory.exists():
            status[target] = "absent"
        elif recorded.get(target) == hash_files(read_local_files(directory)):
            status[target] = "synced"
        else:
            status[target] = "modified" if target in recorded else "untracked"
    return status
