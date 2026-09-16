"""Release awareness: is a newer beakr-cli published, and how is this copy upgraded.

Shared by the CLI (``beakr version``, ``beakr update``, the stale-version notice)
and the MCP server (``beakr_version``, ``update_beakr``) so both surfaces give
the same answer. Before this existed nothing told a user they were behind, which
is how a broken release could sit on machines unnoticed.

The check never blocks real work for long: results are cached in
``~/.beakr/update_check.json`` for a day, the network call has a short timeout,
and any failure degrades to "unknown" rather than an error. Set
``BEAKR_NO_UPDATE_CHECK=1`` to disable the network check entirely.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path

import httpx
from packaging.version import InvalidVersion, Version

from beakr_cli import __version__, config

PACKAGE_NAME = "beakr-cli"
PYPI_JSON_URL = f"https://pypi.org/pypi/{PACKAGE_NAME}/json"
DISABLE_ENV = "BEAKR_NO_UPDATE_CHECK"
DEFAULT_MAX_AGE = timedelta(hours=24)


def _cache_file() -> Path:
    return config.CONFIG_DIR / "update_check.json"


@dataclass(frozen=True)
class UpdateStatus:
    current: str
    latest: str | None
    checked_at: datetime | None

    @property
    def update_available(self) -> bool:
        if self.latest is None:
            return False
        try:
            return Version(self.latest) > Version(self.current)
        except InvalidVersion:
            return False


def check_disabled() -> bool:
    return os.environ.get(DISABLE_ENV, "").strip().lower() in {"1", "true", "yes"}


def fetch_latest_version(timeout: float) -> str | None:
    """Latest non-yanked, non-prerelease version on PyPI, or None on any failure."""
    try:
        resp = httpx.get(PYPI_JSON_URL, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        return None
    candidates: list[Version] = []
    for raw, files in (data.get("releases") or {}).items():
        try:
            version = Version(raw)
        except InvalidVersion:
            continue
        # A release whose every file is yanked is not something to upgrade to.
        if version.is_prerelease or not files or all(f.get("yanked") for f in files):
            continue
        candidates.append(version)
    return str(max(candidates)) if candidates else None


def _read_cache() -> tuple[str | None, datetime | None]:
    try:
        data = json.loads(_cache_file().read_text())
        return data.get("latest"), datetime.fromisoformat(data["checked_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None, None


def _write_cache(latest: str, checked_at: datetime) -> None:
    try:
        config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _cache_file().write_text(
            json.dumps({"latest": latest, "checked_at": checked_at.isoformat()}) + "\n"
        )
    except OSError:
        # A read-only home directory must not break the command being run.
        pass


def get_update_status(
    *,
    max_age: timedelta = DEFAULT_MAX_AGE,
    allow_network: bool = True,
    timeout: float = 2.0,
) -> UpdateStatus:
    """Current vs latest version, from cache when fresh enough, else from PyPI.

    A "latest" older than the running version is never reported or cached. PyPI's
    CDN serves the project JSON with ``max-age=900``, so for up to 15 minutes after
    a release it can still list the previous one; caching that answer used to make
    a freshly upgraded CLI report the version it had just replaced as the latest.
    """
    cached_latest, checked_at = _read_cache()
    if cached_latest is not None and _older_than_running(cached_latest):
        cached_latest, checked_at = None, None
    now = datetime.now(timezone.utc)
    fresh = checked_at is not None and now - checked_at <= max_age
    if fresh or not allow_network or check_disabled():
        return UpdateStatus(__version__, cached_latest, checked_at)
    latest = fetch_latest_version(timeout)
    if latest is None or _older_than_running(latest):
        # Offline, PyPI unavailable, or a stale CDN listing: keep what we last knew.
        return UpdateStatus(__version__, cached_latest, checked_at)
    _write_cache(latest, now)
    return UpdateStatus(__version__, latest, now)


def _older_than_running(version: str) -> bool:
    try:
        return Version(version) < Version(__version__)
    except InvalidVersion:
        return True


# ---------------------------------------------------------------------------
# Install method detection
# ---------------------------------------------------------------------------


class InstallMethod(str, Enum):
    uv_tool = "uv tool"
    uv_tool_source = "uv tool (local source)"
    uv_tool_pinned = "uv tool (pinned version)"
    pipx = "pipx"
    uvx = "uvx"
    other = "other"


@dataclass(frozen=True)
class InstallInfo:
    method: InstallMethod
    #: Command that upgrades this copy in place, or None when that cannot be done
    #: safely on the user's behalf (see ``instructions``).
    upgrade_command: list[str] | None
    instructions: str


def _uv_tool_dir() -> Path:
    if os.environ.get("UV_TOOL_DIR"):
        return Path(os.environ["UV_TOOL_DIR"])
    data_home = os.environ.get("XDG_DATA_HOME")
    base = Path(data_home) if data_home else Path.home() / ".local" / "share"
    return base / "uv" / "tools"


#: The ``{ name = "beakr-cli", ... }`` inline table in a uv tool receipt's
#: ``requirements``. Parsed from the requirement entry alone: the receipt also
#: carries ``entrypoints`` with ``install-path = ...``, and matching keys against
#: the whole file made every uv tool install look like a local checkout.
_RECEIPT_REQUIREMENT = re.compile(r'\{\s*name\s*=\s*"' + re.escape(PACKAGE_NAME) + r'"([^}]*)\}')
_SOURCE_KEYS = re.compile(r"(?<![\w-])(directory|path|git|url|editable)\s*=")
_PINNED_SPECIFIER = re.compile(r'(?<![\w-])specifier\s*=\s*"\s*==')


def _receipt_requirement(receipt: Path) -> str | None:
    """The body of this package's requirement entry in a uv receipt, if readable."""
    try:
        match = _RECEIPT_REQUIREMENT.search(receipt.read_text())
    except OSError:
        return None
    return match.group(1) if match else None


def _receipt_is_local_source(requirement: str) -> bool:
    """True when the uv tool was installed from a directory, path, URL, or git.

    Upgrading such an install from PyPI would silently swap a developer's local
    build for the release, so it is reported rather than done.
    """
    return _SOURCE_KEYS.search(requirement) is not None


def _receipt_pins_version(requirement: str) -> bool:
    """True when the uv tool was installed as ``beakr-cli==X``.

    ``uv tool upgrade`` honours the pin and does nothing, so offering it would
    report an upgrade that never happens.
    """
    return _PINNED_SPECIFIER.search(requirement) is not None


def detect_install(prefix: Path | None = None) -> InstallInfo:
    """Work out how this interpreter's beakr-cli was installed."""
    env = (prefix or Path(sys.prefix)).resolve()
    parts = env.parts

    try:
        in_uv_tools = env.parent == _uv_tool_dir().resolve()
    except OSError:
        in_uv_tools = False
    if in_uv_tools or ("uv" in parts and "tools" in parts):
        requirement = _receipt_requirement(env / "uv-receipt.toml") or ""
        if _receipt_is_local_source(requirement):
            return InstallInfo(
                InstallMethod.uv_tool_source,
                None,
                "Installed with uv from a local checkout. Rebuild it with "
                "`uv tool install --force <checkout>`, or switch to the published "
                f"release with `uv tool install --force {PACKAGE_NAME}`.",
            )
        if _receipt_pins_version(requirement):
            return InstallInfo(
                InstallMethod.uv_tool_pinned,
                None,
                "Installed with uv pinned to one version, which `uv tool upgrade` keeps. "
                f"Unpin it with `uv tool install --force {PACKAGE_NAME}`.",
            )
        # --reinstall-package implies --refresh-package (which `uv tool upgrade` does not
        # accept directly). uv caches the package index, and without a refresh an
        # upgrade run shortly after a release resolves the old version and exits 0.
        return InstallInfo(
            InstallMethod.uv_tool,
            ["uv", "tool", "upgrade", "--reinstall-package", PACKAGE_NAME, PACKAGE_NAME],
            f"Run `beakr update` (or `uv tool upgrade {PACKAGE_NAME}`).",
        )

    pipx_home = os.environ.get("PIPX_HOME")
    in_custom_pipx = bool(pipx_home) and env.parent == (Path(pipx_home) / "venvs").resolve()
    if in_custom_pipx or ("pipx" in parts and "venvs" in parts):
        return InstallInfo(
            InstallMethod.pipx,
            ["pipx", "upgrade", "--pip-args=--no-cache-dir", PACKAGE_NAME],
            f"Run `beakr update` (or `pipx upgrade {PACKAGE_NAME}`).",
        )

    if "uv" in parts and ("archive-v0" in parts or "environments-v2" in parts):
        return InstallInfo(
            InstallMethod.uvx,
            None,
            "Running through uvx, which reuses a cached copy. Use "
            f"`uvx --from {PACKAGE_NAME}@latest beakr mcp` in the MCP config to always "
            "run the newest release, or refresh once with "
            f"`uvx --refresh --from {PACKAGE_NAME} beakr`.",
        )

    return InstallInfo(
        InstallMethod.other,
        None,
        f"Upgrade with the tool that installed it, e.g. `pip install --upgrade {PACKAGE_NAME}` "
        "inside the same environment.",
    )


# ---------------------------------------------------------------------------
# Upgrade
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UpgradeResult:
    ok: bool
    message: str
    output: str


def _beakr_executable() -> str | None:
    return shutil.which("beakr")


def run_upgrade(
    info: InstallInfo, *, expected_version: str | None = None, timeout: float = 300.0
) -> UpgradeResult:
    """Upgrade the package, confirm it moved, then refresh the skills it installed.

    The skill refresh runs through the NEW binary on PATH, not this process: the
    running interpreter still has the old code and the old bundled assets loaded.
    A package manager exiting 0 is not proof of an upgrade (a cached index or a
    pin both "succeed" without changing anything), so the binary's own version is
    checked against ``expected_version`` before anything is reported as upgraded.
    """
    if info.upgrade_command is None:
        return UpgradeResult(False, info.instructions, "")
    tool = info.upgrade_command[0]
    if shutil.which(tool) is None:
        return UpgradeResult(
            False,
            f"`{tool}` is not on PATH, so the upgrade cannot run here. {info.instructions}",
            "",
        )

    try:
        upgrade = subprocess.run(
            info.upgrade_command, capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        return UpgradeResult(False, f"`{' '.join(info.upgrade_command)}` timed out.", "")
    output = (upgrade.stdout + upgrade.stderr).strip()
    if upgrade.returncode != 0:
        return UpgradeResult(
            False, f"`{' '.join(info.upgrade_command)}` failed (exit {upgrade.returncode}).", output
        )

    if expected_version is not None:
        now = installed_version_on_path()
        if now is None or _version_lt(now, expected_version):
            return UpgradeResult(
                False,
                f"The upgrade ran but `beakr` on PATH is still {now or 'unknown'}, not "
                f"{expected_version}. A new release can take a few minutes to reach PyPI's "
                "mirrors; try again shortly.",
                output,
            )

    beakr = _beakr_executable()
    if beakr is None:
        return UpgradeResult(
            True,
            "Upgraded, but `beakr` is not on PATH so installed skills were not refreshed. "
            "Run `beakr install --refresh` once it is.",
            output,
        )
    try:
        refresh = subprocess.run(
            [beakr, "install", "--refresh"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return UpgradeResult(
            True,
            "Upgraded, but refreshing skills timed out. Run `beakr install --refresh`.",
            output,
        )
    output = "\n".join(part for part in (output, (refresh.stdout + refresh.stderr).strip()) if part)
    if refresh.returncode != 0:
        return UpgradeResult(
            True,
            "Upgraded, but refreshing skills failed. Run `beakr install --refresh`.",
            output,
        )
    return UpgradeResult(True, "Upgraded and refreshed installed skills.", output)


def installed_version_on_path() -> str | None:
    """Version reported by the `beakr` binary on PATH (the one clients will launch)."""
    beakr = _beakr_executable()
    if beakr is None:
        return None
    try:
        result = subprocess.run(
            [beakr, "version", "--offline"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None
    text = result.stdout.strip().splitlines()
    if result.returncode != 0 or not text:
        return None
    # First line is "beakr-cli X.Y.Z"; later lines may describe update status.
    return text[0].removeprefix(f"{PACKAGE_NAME} ").strip() or None


def _version_lt(left: str, right: str) -> bool:
    try:
        return Version(left) < Version(right)
    except InvalidVersion:
        return True
