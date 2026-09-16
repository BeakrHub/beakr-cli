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
    """Current vs latest version, from cache when fresh enough, else from PyPI."""
    cached_latest, checked_at = _read_cache()
    now = datetime.now(timezone.utc)
    fresh = checked_at is not None and now - checked_at <= max_age
    if fresh or not allow_network or check_disabled():
        return UpdateStatus(__version__, cached_latest, checked_at)
    latest = fetch_latest_version(timeout)
    if latest is None:
        # Offline or PyPI unavailable: fall back to what we last knew.
        return UpdateStatus(__version__, cached_latest, checked_at)
    _write_cache(latest, now)
    return UpdateStatus(__version__, latest, now)


# ---------------------------------------------------------------------------
# Install method detection
# ---------------------------------------------------------------------------


class InstallMethod(str, Enum):
    uv_tool = "uv tool"
    uv_tool_source = "uv tool (local source)"
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


def _receipt_is_local_source(receipt: Path) -> bool:
    """True when the uv tool was installed from a directory, path, or git checkout.

    Upgrading such an install from PyPI would silently swap a developer's local
    build for the release, so it is reported rather than done.
    """
    try:
        text = receipt.read_text()
    except OSError:
        return False
    return any(marker in text for marker in ("directory =", "path =", "git ="))


def detect_install(prefix: Path | None = None) -> InstallInfo:
    """Work out how this interpreter's beakr-cli was installed."""
    env = (prefix or Path(sys.prefix)).resolve()
    parts = env.parts

    try:
        in_uv_tools = env.parent == _uv_tool_dir().resolve()
    except OSError:
        in_uv_tools = False
    if in_uv_tools or ("uv" in parts and "tools" in parts):
        receipt = env / "uv-receipt.toml"
        if _receipt_is_local_source(receipt):
            return InstallInfo(
                InstallMethod.uv_tool_source,
                None,
                "Installed with uv from a local checkout. Rebuild it with "
                "`uv tool install --force <checkout>`, or switch to the published "
                f"release with `uv tool install --force {PACKAGE_NAME}`.",
            )
        return InstallInfo(
            InstallMethod.uv_tool,
            ["uv", "tool", "upgrade", PACKAGE_NAME],
            f"Run `beakr update` (or `uv tool upgrade {PACKAGE_NAME}`).",
        )

    if "pipx" in parts and "venvs" in parts:
        return InstallInfo(
            InstallMethod.pipx,
            ["pipx", "upgrade", PACKAGE_NAME],
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


def run_upgrade(info: InstallInfo, *, timeout: float = 300.0) -> UpgradeResult:
    """Upgrade the package, then refresh the skills it installed.

    The skill refresh runs through the NEW binary on PATH, not this process: the
    running interpreter still has the old code and the old bundled assets loaded.
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
            [beakr, "version"], capture_output=True, text=True, timeout=30, check=False
        )
    except subprocess.TimeoutExpired:
        return None
    text = result.stdout.strip().splitlines()
    if result.returncode != 0 or not text:
        return None
    # First line is "beakr-cli X.Y.Z"; later lines may describe update status.
    return text[0].removeprefix(f"{PACKAGE_NAME} ").strip() or None
