"""The client versions this installation's release ships, and the line that says so.

A full self-hosted deployment serves its own release's clients from the same host
as the app (beakr-engine ``client_index.py``, served by the web image):
``/clients/manifest.json`` names the release and, per package, the exact stamped
version (``0.7.0+selfhosted.g<commit>``), and ``/pypi/simple/`` is a PEP 503 index
holding exactly those wheels. When the installation is upgraded, its manifest
names new versions; this module notices that the running CLI is no longer the one
the installation ships and prints one line saying which version to install and how.

Only a self-hosted build asks (``updates.self_hosted_build``): a public CLI is
upgraded from PyPI, and hosted Beakr serves no manifest. The check never gets in
the way of the command being run:

- the manifest is cached in ``~/.beakr/installation_clients.json`` per installation
  for a day, and a failed fetch is not retried for an hour (the attempt is recorded
  before the request, so a host that never answers is asked once an hour at most);
- the request has a short timeout, runs in a daemon thread at startup, and the
  command waits for it at most what is left of a 1.5 s budget when it finishes;
- any failure (no URL, unreachable, 404, not JSON, another schema) is silence;
- the line is printed at most once a day per installation and shipped version.

The upgrade command pins both Python packages exactly: ``beakr-cli`` requires
``beakr-sdk``, and a ``+selfhosted.`` local version cannot exist on PyPI, so the
pins resolve only from the installation's index while third-party dependencies
still come from the person's own index or mirror (``--extra-index-url``).
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from beakr_cli import config

MANIFEST_PATH = "/clients/manifest.json"
INDEX_PATH = "/pypi/simple"
SCHEMA_VERSION = 1
CLI_PACKAGE = "beakr-cli"
SDK_PACKAGE = "beakr-sdk"
#: A manifest read successfully is trusted for this long.
MAX_AGE = timedelta(hours=24)
#: After an attempt (successful or not), wait this long before asking again when
#: there is no fresh manifest.
RETRY_AFTER = timedelta(hours=1)
#: The same notice is printed at most this often.
NOTICE_EVERY = timedelta(hours=24)
FETCH_TIMEOUT = 1.5
#: The most a command waits, in total from startup, for a refresh it started.
STARTUP_BUDGET = 1.5

_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _version() -> str:
    # Read through ``updates`` so a test that stamps a self-hosted version there
    # stamps it here too.
    from beakr_cli import updates

    return updates.__version__


@dataclass(frozen=True)
class Expected:
    """What the installation at ``base_url`` ships, from its manifest."""

    base_url: str
    release: str
    cli: str
    sdk: str | None = None

    @property
    def matches(self) -> bool:
        return self.cli == _version()


def parse_manifest(data: Any) -> tuple[str, str, str | None] | None:
    """``(release, beakr-cli version, beakr-sdk version)``, or None for anything else."""
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        return None
    release = data.get("release")
    clients = data.get("clients")
    if not isinstance(release, str) or not release or not isinstance(clients, dict):
        return None

    def version(package: str) -> str | None:
        entry = clients.get(package)
        value = entry.get("version") if isinstance(entry, dict) else None
        return value if isinstance(value, str) and value else None

    cli = version(CLI_PACKAGE)
    if cli is None:
        return None
    return release, cli, version(SDK_PACKAGE)


def fetch(base_url: str, timeout: float = FETCH_TIMEOUT) -> Expected | None:
    """The installation's manifest, or None on any failure."""
    try:
        response = httpx.get(
            base_url.rstrip("/") + MANIFEST_PATH,
            timeout=timeout,
            follow_redirects=False,
            headers={"Accept": "application/json"},
        )
        if response.status_code != 200:
            return None
        parsed = parse_manifest(response.json())
    except Exception:
        return None
    if parsed is None:
        return None
    release, cli, sdk = parsed
    return Expected(base_url, release, cli, sdk)


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def _cache_file() -> Path:
    return config.config_dir() / "installation_clients.json"


def _read_all() -> dict[str, dict]:
    try:
        data = json.loads(_cache_file().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_all(data: dict[str, dict]) -> None:
    """Replace the cache atomically; a read-only home only loses the cache."""
    name: str | None = None
    try:
        directory = config.ensure_dir()
        handle, name = tempfile.mkstemp(dir=directory, prefix=".installation_clients.")
        with os.fdopen(handle, "w") as stream:
            stream.write(json.dumps(data, indent=2) + "\n")
        os.replace(name, _cache_file())
    except OSError:
        if name is not None:
            with contextlib.suppress(OSError):
                os.unlink(name)


def _update(base_url: str, change: Callable[[dict], None]) -> None:
    with _lock:
        data = _read_all()
        entry = data.get(base_url)
        entry = entry if isinstance(entry, dict) else {}
        change(entry)
        data[base_url] = entry
        _write_all(data)


def _time(entry: dict, key: str) -> datetime | None:
    try:
        return datetime.fromisoformat(entry[key])
    except (KeyError, TypeError, ValueError):
        return None


def cached(base_url: str) -> Expected | None:
    """The last manifest read for ``base_url`` while it is fresh, else None."""
    entry = _read_all().get(base_url)
    if not isinstance(entry, dict):
        return None
    checked = _time(entry, "checked_at")
    if checked is None or _now() - checked > MAX_AGE:
        return None
    parsed = parse_manifest(entry.get("manifest"))
    if parsed is None:
        return None
    return Expected(base_url, *parsed)


def needs_refresh(base_url: str) -> bool:
    """No fresh manifest, and no attempt within :data:`RETRY_AFTER`."""
    if cached(base_url) is not None:
        return False
    entry = _read_all().get(base_url)
    attempted = _time(entry, "attempted_at") if isinstance(entry, dict) else None
    return attempted is None or _now() - attempted > RETRY_AFTER


def refresh(base_url: str, timeout: float = FETCH_TIMEOUT) -> Expected | None:
    """Ask the installation now; the attempt is recorded before the request."""
    _update(base_url, lambda entry: entry.__setitem__("attempted_at", _now().isoformat()))
    found = fetch(base_url, timeout)
    if found is not None:

        def store(entry: dict) -> None:
            entry["checked_at"] = _now().isoformat()
            entry["manifest"] = {
                "schema_version": SCHEMA_VERSION,
                "release": found.release,
                "clients": {
                    CLI_PACKAGE: {"version": found.cli},
                    **({SDK_PACKAGE: {"version": found.sdk}} if found.sdk else {}),
                },
            }

        _update(base_url, store)
    return found


def expected(
    base_url: str | None = None,
    *,
    allow_network: bool = True,
    timeout: float = FETCH_TIMEOUT,
    force: bool = False,
) -> Expected | None:
    """What the configured installation ships: cache first, then (if allowed) the network.

    ``force`` asks even within an hour of a failed attempt: for a command the person
    ran to find out (``beakr doctor``, ``beakr version``), not for a startup check.
    """
    base_url = base_url or _base_url()
    if not base_url:
        return None
    try:
        found = cached(base_url)
        if found is None and allow_network and (force or needs_refresh(base_url)):
            found = refresh(base_url, timeout)
        return found
    except Exception:
        return None


def _base_url() -> str | None:
    from beakr_cli.deployment import configured_base_url

    try:
        return configured_base_url()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Wording
# ---------------------------------------------------------------------------


def _uses_uv_tool() -> bool:
    from beakr_cli.updates import InstallMethod, detect_install

    try:
        method = detect_install().method
    except Exception:
        return False
    return method in (
        InstallMethod.uv_tool,
        InstallMethod.uv_tool_source,
        InstallMethod.uv_tool_pinned,
    )


def upgrade_command(found: Expected, *, uv_tool: bool | None = None) -> str:
    """The command that installs exactly the clients ``found`` names, from its index."""
    index = found.base_url.rstrip("/") + INDEX_PATH
    cli = f'"{CLI_PACKAGE}=={found.cli}"'
    sdk = f'"{SDK_PACKAGE}=={found.sdk}"' if found.sdk else ""
    if _uses_uv_tool() if uv_tool is None else uv_tool:
        with_sdk = f" --with {sdk}" if sdk else ""
        return f"uv tool install --force --extra-index-url {index} {cli}{with_sdk}"
    return f"pip install -U --extra-index-url {index} {cli}" + (f" {sdk}" if sdk else "")


def mismatch_line(found: Expected | None, *, uv_tool: bool | None = None) -> str | None:
    """The one-line notice, or None when the CLI is what the installation ships."""
    if found is None or found.matches:
        return None
    return (
        f"This installation (release {found.release}) ships {CLI_PACKAGE} {found.cli}; "
        f"you have {_version()}. Upgrade: {upgrade_command(found, uv_tool=uv_tool)}"
    )


def version_line(found: Expected | None) -> str | None:
    """For ``beakr version``: the shipped version, and the upgrade when it differs."""
    if found is None:
        return None
    return mismatch_line(found) or (
        f"This installation (release {found.release}) ships {CLI_PACKAGE} {found.cli}: this one."
    )


# ---------------------------------------------------------------------------
# The startup notice
# ---------------------------------------------------------------------------


def _notified_recently(found: Expected) -> bool:
    entry = _read_all().get(found.base_url)
    notified = entry.get("notified") if isinstance(entry, dict) else None
    if not isinstance(notified, dict) or notified.get("version") != found.cli:
        return False
    when = _time(notified, "at")
    return when is not None and _now() - when <= NOTICE_EVERY


def _mark_notified(found: Expected) -> None:
    _update(
        found.base_url,
        lambda entry: entry.__setitem__(
            "notified", {"version": found.cli, "at": _now().isoformat()}
        ),
    )


class StartupNotice:
    """Started before a command runs, finished after it: never holds the command up.

    ``start`` reads the cache and, when it is stale, refreshes it in a daemon
    thread. ``finish`` waits for that thread at most what is left of the budget,
    then prints the line through ``emit`` if the CLI differs from what the
    installation ships and the same line was not printed in the last day.
    """

    def __init__(self, base_url: str, *, budget: float = STARTUP_BUDGET) -> None:
        self.base_url = base_url
        self.budget = budget
        self.started = time.monotonic()
        self.found: Expected | None = None
        self.thread: threading.Thread | None = None

    def start(self) -> StartupNotice:
        try:
            self.found = cached(self.base_url)
            if self.found is None and needs_refresh(self.base_url):
                self.thread = threading.Thread(
                    target=self._refresh, name="beakr-installation-clients", daemon=True
                )
                self.thread.start()
        except Exception:
            self.thread = None
        return self

    def _refresh(self) -> None:
        with contextlib.suppress(Exception):
            self.found = refresh(self.base_url, timeout=self.budget)

    def finish(self, emit: Callable[[str], None]) -> None:
        try:
            if self.thread is not None:
                left = self.budget - (time.monotonic() - self.started)
                self.thread.join(max(0.0, left))
                if self.thread.is_alive():
                    return
            line = mismatch_line(self.found)
            if line is None or _notified_recently(self.found):
                return
            _mark_notified(self.found)
            emit(line)
        except Exception:
            return


def start_notice(base_url: str | None = None) -> StartupNotice | None:
    """A started :class:`StartupNotice` for a self-hosted build with a URL, else None."""
    from beakr_cli.updates import self_hosted_build

    if not self_hosted_build():
        return None
    base_url = base_url or _base_url()
    if not base_url:
        return None
    return StartupNotice(base_url).start()


def server_log_line(timeout: float = FETCH_TIMEOUT) -> str | None:
    """For the stdio MCP server's startup log (stderr): the line on every mismatch."""
    from beakr_cli.updates import self_hosted_build

    if not self_hosted_build():
        return None
    return mismatch_line(expected(allow_network=True, timeout=timeout))
