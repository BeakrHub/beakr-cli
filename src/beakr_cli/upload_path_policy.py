"""Which local paths the ``upload_files`` MCP tool will send, and why not the rest.

The tool takes paths from an agent, and an agent can be steered by text it read
(a web page, a document, a tool result). Uploading is the one tool here that
moves the person's own files off their computer, so what it will not send is
decided by where the file is and what it is called, not by who asked:

- credential and system locations are never sent: the system's own folders
  (``/etc``, ``/var``, ...), a home folder's hidden folders (``~/.ssh``,
  ``~/.aws``, ``~/.config``), and the application data that holds keychains and
  browser profiles (``~/Library`` apart from the cloud drives under it);
- a hidden file is never sent;
- a file named like a credential (``prod.env``, ``terraform.tfstate``,
  ``kubeconfig``, ``service-account.json``, ``server.pem``) is never sent,
  wherever it is and whether the call named it or a folder holding it. The
  working directory is no exception: an agent's can be ``/`` or the home folder;
- a link inside a folder being uploaded that leads outside that folder is not
  followed (:func:`outside_link`): a folder is what is in it.

Places are compared without case on macOS and Windows, whose filesystems have
none (``~/.SSH``, ``/ETC`` and ``~/library`` are the same places), and names
without case everywhere (``ID_RSA``, ``Prod.ENV``).

Each refusal is a reason the tool reports for that path, and says that a person
who does mean to upload the file can run ``beakr upload`` in a terminal, which
sends what it is given. An ordinary upload (a
project folder, named documents from the working directory or from Documents,
Desktop or Downloads) meets none of them.
"""

from __future__ import annotations

import fnmatch
import sys
import tempfile
from pathlib import Path

__all__ = ["BY_HAND", "MAX_UPLOAD_BYTES", "outside_link", "refused_path"]

#: What every refusal ends with. ``beakr upload`` is the person's own command:
#: it sends the paths it is given and does not apply these rules.
BY_HAND = (
    "An agent cannot upload it; a person who means to can run "
    "`beakr upload PATH --project PROJECT` in a terminal"
)

#: Bytes one ``upload_files`` call sends: room for one file of the largest size
#: Beakr takes (1 GB) and a folder of documents beside it.
MAX_UPLOAD_BYTES = 2 * 1024**3

#: Folders never uploaded from, wherever they sit: credential stores, settings, git's own.
_SECRET_DIRECTORIES = frozenset(
    {
        ".ssh",
        ".aws",
        ".azure",
        ".gnupg",
        ".kube",
        ".docker",
        ".beakr",
        ".config",
        ".git",
        ".password-store",
        ".terraform",
    }
)
#: The system's own folders: configuration, state, devices, other accounts.
_SYSTEM_ROOTS = (
    "/etc",
    "/private/etc",
    "/var",
    "/private/var",
    "/proc",
    "/sys",
    "/dev",
    "/boot",
    "/Library/Keychains",
    "/System",
)
#: Under a home folder: application data, where keychains, browser profiles,
#: cookies, mail and messages live.
_HOME_APPLICATION_DATA = ("Library", "AppData")
#: The parts of ``~/Library`` that are a person's documents: their cloud drives.
_HOME_DOCUMENT_STORES = (("Library", "Mobile Documents"), ("Library", "CloudStorage"))
#: File names that say "credential": keys, keystores, environment and state
#: files, service accounts, tool logins. Matched on the casefolded name.
_CREDENTIAL_NAMES = (
    "*.env",
    ".env*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.jks",
    "*.keystore",
    "*.kdbx",
    "*.ovpn",
    "id_*",
    "*.tfstate*",
    "*.tfvars",
    "kubeconfig*",
    "*.kubeconfig",
    "*credentials*",
    "*service-account*.json",
    "*service_account*.json",
    "client_secret*.json",
    "secrets.*",
    "*.secret",
    "htpasswd",
    ".netrc",
    ".npmrc",
    ".pypirc",
)


def _folds_case() -> bool:
    """Whether this platform's filesystems treat ``/ETC`` and ``/etc`` as one place."""
    return sys.platform in ("darwin", "win32")


def _place(part: str) -> str:
    """A path part as places are compared: without case where the filesystem has none."""
    return part.casefold() if _folds_case() else part


def _parts(path: Path | str) -> tuple[str, ...]:
    return tuple(_place(part) for part in Path(path).parts)


def _below(path: Path, root: Path | str) -> tuple[str, ...] | None:
    """The parts of ``path`` under ``root`` (as places are compared), or None when it is not."""
    parts, base = _parts(path), _parts(root)
    return parts[len(base) :] if parts[: len(base)] == base else None


def _system_location(path: Path) -> str | None:
    temporary = Path(tempfile.gettempdir())
    if _below(path, temporary) is not None or _below(path, temporary.resolve()) is not None:
        # The temporary folder sits under /var on macOS and holds no system state.
        return None
    for root in _SYSTEM_ROOTS:
        if _below(path, root) is not None:
            return f"it is inside {root}, which belongs to the system"
    return None


def _home_location(path: Path) -> str | None:
    below_home = _below(path, Path.home())
    if not below_home:
        return None
    if below_home[0].startswith("."):
        return f"~/{below_home[0]} holds settings and credentials"
    if below_home[0] in {_place(name) for name in _HOME_APPLICATION_DATA} and not any(
        below_home[: len(store)] == _parts(Path(*store)) for store in _HOME_DOCUMENT_STORES
    ):
        return (
            f"~/{below_home[0]} holds application data (keychains, browser profiles, cookies), "
            "not documents"
        )
    return None


def _credential_name(name: str) -> bool:
    # On every platform: a file called ID_RSA or Prod.ENV is still a credential.
    folded = name.casefold()
    return any(fnmatch.fnmatchcase(folded, pattern) for pattern in _CREDENTIAL_NAMES)


def _why_not(path: Path) -> str | None:
    reason = _system_location(path)
    if reason:
        return reason
    secret = {_place(name) for name in _SECRET_DIRECTORIES}
    for part in _parts(path):
        if part in secret:
            return f"it is inside {part}, which holds credentials or settings"
    if path.name.startswith("."):
        return f"{path.name} is a hidden file"
    reason = _home_location(path)
    if reason:
        return reason
    if _credential_name(path.name):
        return f"{path.name} looks like a key or credential file"
    return None


def refused_path(path: Path) -> str | None:
    """Why ``path`` may not be uploaded by an agent, or None.

    Checked for the path as given and for the one a symlink leads to, wherever
    it is and however the call named it. A hidden folder elsewhere (a checkout
    under ``~/work/.worktrees``) is an ordinary place. The reason ends with
    what a person who does mean to upload the file can do.
    """
    for candidate in (path, path.resolve()):
        reason = _why_not(candidate)
        if reason:
            return f"{reason}. {BY_HAND}"
    return None


def outside_link(file_path: Path, folder: Path) -> str | None:
    """Why a file found under ``folder`` is not part of it, or None.

    A symlink (the file itself, or a folder on the way to it) that leads out of
    the folder being uploaded would send a file the person did not point at.
    """
    if _below(file_path.resolve(), folder.resolve()) is None:
        return f"it is a link to a file outside {folder.name or folder}, the folder being uploaded"
    return None
