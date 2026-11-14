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
  ``kubeconfig``, ``service-account.json``, ``server.pem``) is sent only when
  it is under the working directory and the call names that exact file. Found
  on a folder walk, or named from anywhere else, it stays;
- a link inside a folder being uploaded that leads outside that folder is not
  followed (:func:`outside_link`): a folder is what is in it.

Each refusal is a reason the tool reports for that path. An ordinary upload (a
project folder, named documents from the working directory or from Documents,
Desktop or Downloads) meets none of them.
"""

from __future__ import annotations

import fnmatch
import tempfile
from pathlib import Path

__all__ = ["MAX_UPLOAD_BYTES", "outside_link", "refused_path"]

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
#: files, service accounts, tool logins. Matched on the lower-cased name.
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


def _below(path: Path, root: Path) -> tuple[str, ...] | None:
    try:
        return path.relative_to(root).parts
    except ValueError:
        return None


def _system_location(path: Path) -> str | None:
    temporary = Path(tempfile.gettempdir())
    if _below(path, temporary) is not None or _below(path, temporary.resolve()) is not None:
        # The temporary folder sits under /var on macOS and holds no system state.
        return None
    for root in _SYSTEM_ROOTS:
        if _below(path, Path(root)) is not None:
            return f"it is inside {root}, which belongs to the system"
    return None


def _home_location(path: Path) -> str | None:
    below_home = _below(path, Path.home())
    if not below_home:
        return None
    if below_home[0].startswith("."):
        return f"~/{below_home[0]} holds settings and credentials"
    if below_home[0] in _HOME_APPLICATION_DATA and not any(
        below_home[: len(store)] == store for store in _HOME_DOCUMENT_STORES
    ):
        return (
            f"~/{below_home[0]} holds application data (keychains, browser profiles, cookies), "
            "not documents"
        )
    return None


def _credential_name(name: str) -> bool:
    return any(fnmatch.fnmatch(name.lower(), pattern) for pattern in _CREDENTIAL_NAMES)


def refused_path(path: Path, *, named: bool = False) -> str | None:
    """Why ``path`` may not be uploaded by an agent, or None.

    Checked for the path as given and for the one a symlink leads to. ``named``
    is whether the call gave this exact file (not a folder it sits in): a file
    named like a credential is sent only then, and only from under the working
    directory. A hidden folder elsewhere (a checkout under
    ``~/work/.worktrees``) is an ordinary place.
    """
    resolved = path.resolve()
    chosen = named and _below(resolved, Path.cwd().resolve()) is not None
    for candidate in (path, resolved):
        reason = _system_location(candidate)
        if reason:
            return reason
        for part in candidate.parts:
            if part in _SECRET_DIRECTORIES:
                return f"it is inside {part}, which holds credentials or settings"
        if candidate.name.startswith("."):
            return f"{candidate.name} is a hidden file"
        reason = _home_location(candidate)
        if reason:
            return reason
        if _credential_name(candidate.name) and not chosen:
            return (
                f"{candidate.name} looks like a key or credential file; one is uploaded only "
                "when it is under the working directory and the call names that exact file"
            )
    return None


def outside_link(file_path: Path, folder: Path) -> str | None:
    """Why a file found under ``folder`` is not part of it, or None.

    A symlink (the file itself, or a folder on the way to it) that leads out of
    the folder being uploaded would send a file the person did not point at.
    """
    if _below(file_path.resolve(), folder.resolve()) is None:
        return f"it is a link to a file outside {folder.name or folder}, the folder being uploaded"
    return None
