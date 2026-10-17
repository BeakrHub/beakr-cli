"""Bind a working directory to a Beakr project (``.beakr/project.json``).

``beakr init`` writes the file; every command that takes ``--project`` falls
back to it, so inside a bound repository ``beakr upload docs/`` and the
session hooks land in the right project without flags. Resolution order:
explicit flag, ``BEAKR_PROJECT_ID``, the nearest binding file walking up from
the current directory, then the CLI's default project.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

BINDING_DIR = ".beakr"
BINDING_FILE = "project.json"

#: ``--project auto`` means "whatever the resolution order finds", which is what
#: hooks pass so they never hard-code an id.
AUTO = "auto"


def find_binding(start: Path | None = None) -> Path | None:
    """The nearest ``.beakr/project.json`` at or above ``start``."""
    current = (start or Path.cwd()).resolve()
    home_binding_dir = Path.home().resolve()
    for directory in (current, *current.parents):
        candidate = directory / BINDING_DIR / BINDING_FILE
        # ~/.beakr is the CLI's own config directory, never a project binding.
        if directory == home_binding_dir:
            continue
        if candidate.is_file():
            return candidate
    return None


def read_binding(start: Path | None = None) -> dict[str, Any] | None:
    path = find_binding(start)
    if path is None:
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_binding(directory: Path, data: dict[str, Any]) -> Path:
    path = directory / BINDING_DIR / BINDING_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    return path


# ---------------------------------------------------------------------------
# Per-project consent to automatic session capture (spec step 8.1 item 3)
# ---------------------------------------------------------------------------

#: ``.beakr/project.json`` key recording that sessions run in this repository
#: may be captured automatically. Installing the hooks is the per-user choice;
#: this is the per-project one. Without it, hooks never upload a transcript.
CAPTURE_FIELD = "capture"


def capture_enabled(cwd: str | Path | None) -> bool:
    """Whether a session that ran in ``cwd`` may be captured automatically.

    True only when the nearest binding at or above ``cwd`` sets ``capture``
    to ``true`` and applies to the credential in use. An unbound directory,
    a binding without consent, or one made for another API or organization
    is never captured; a deliberate ``beakr compound --from-transcript`` or
    ``beakr conversations import`` is not affected.
    """
    if not cwd:
        return False
    binding = read_binding(Path(cwd))
    return bool(binding and binding.get(CAPTURE_FIELD) is True and binding_applies(binding))


def set_capture(start: Path, enabled: bool) -> Path:
    """Record (or withdraw) capture consent in the nearest binding; LookupError if unbound."""
    path = find_binding(start)
    if path is None:
        raise LookupError(f"{start} is not bound to a Beakr project; run `beakr init` there first.")
    data = read_binding(start) or {}
    data[CAPTURE_FIELD] = bool(enabled)
    path.write_text(json.dumps(data, indent=2) + "\n")
    return path


LEARNING_FIELD = "learn_from_sessions"


def learning_enabled(cwd: str | Path | None) -> bool:
    """Whether captured sessions from ``cwd`` may teach the person's personal learning.

    A separate opt-in from capture consent: capture sends the session to the
    project, learning additionally marks a session that a person typed in as
    ``interactive`` so the engine may learn the person's preferences from it.
    True only when the nearest binding sets ``learn_from_sessions`` to
    ``true`` and applies to the credential in use. Off by default, and reset
    when the directory is rebound to a different project.
    """
    if not cwd:
        return False
    binding = read_binding(Path(cwd))
    return bool(binding and binding.get(LEARNING_FIELD) is True and binding_applies(binding))


def set_learning(start: Path, enabled: bool) -> Path:
    """Record (or withdraw) the learning opt-in in the nearest binding; LookupError if unbound."""
    path = find_binding(start)
    if path is None:
        raise LookupError(f"{start} is not bound to a Beakr project; run `beakr init` there first.")
    data = read_binding(start) or {}
    data[LEARNING_FIELD] = bool(enabled)
    path.write_text(json.dumps(data, indent=2) + "\n")
    return path


def resolve_project(explicit: str | None = None) -> str | None:
    """The project id a command should use, or None for org-wide.

    ``explicit`` and ``BEAKR_PROJECT_ID`` may be a project id, slug or exact
    name (:func:`beakr_cli.project_ref.resolve_project_ref`).
    """
    from beakr_cli import config
    from beakr_cli.project_ref import resolve_project_ref

    if explicit and explicit != AUTO:
        return resolve_project_ref(explicit)
    env_project = os.environ.get("BEAKR_PROJECT_ID")
    if env_project:
        return resolve_project_ref(env_project, param_hint="BEAKR_PROJECT_ID")
    binding = read_binding()
    if binding and binding.get("project_id") and binding_applies(binding):
        return str(binding["project_id"])
    configured = config.get("project_id")
    return str(configured) if configured else None


def project_is_pinned() -> bool:
    """Whether this process works in one project fixed outside the agent's control.

    ``BEAKR_PROJECT_ID``, or a binding file that applies to the credential in
    use. The CLI's default project is not a pin: it is a fallback the user set
    once, not a statement that this session works in that project.
    """
    if os.environ.get("BEAKR_PROJECT_ID"):
        return True
    binding = read_binding()
    return bool(binding and binding.get("project_id") and binding_applies(binding))


# ---------------------------------------------------------------------------
# Does a binding belong to the credential in use?
# ---------------------------------------------------------------------------

#: Credential fingerprint -> the organization it acts in, so the lookup behind
#: :func:`credential_org_id` costs one request per credential, not per command.
_ORG_CACHE_FILE = "credential_orgs.json"


def binding_applies(binding: dict[str, Any]) -> bool:
    """A binding applies only to the API and organization it was made for.

    A directory bound with one credential used to hand its project to any
    other: a key for another organization, or another server, then sent a
    project id it cannot see. A binding records ``base_url`` and ``org_id`` at
    ``beakr init``; when either differs from the credential in use the binding
    is ignored, as if the directory were unbound. An org that cannot be
    determined (offline, an older binding without ``org_id``) keeps the binding.
    """
    from beakr_sdk.credentials import normalize_origin, resolve_base_url

    bound_url = binding.get("base_url")
    if bound_url and normalize_origin(str(bound_url)) != normalize_origin(resolve_base_url()):
        return False
    bound_org = binding.get("org_id")
    if not bound_org:
        return True
    current = credential_org_id()
    return current is None or current == str(bound_org)


def credential_org_id() -> str | None:
    """The organization the configured credential acts in, or None if unknown."""
    import hashlib

    from beakr_sdk.credentials import beakr_home, resolve_base_url, resolve_credential
    from beakr_sdk.errors import BeakrError

    try:
        credential = resolve_credential()
    except BeakrError:
        return None
    if credential.tokens is not None and credential.tokens.org_id:
        return credential.tokens.org_id
    secret = credential.token
    if not secret:
        return None
    base_url = resolve_base_url()
    fingerprint = hashlib.sha256(f"{base_url}\n{secret}".encode()).hexdigest()[:32]
    cache_path = beakr_home() / _ORG_CACHE_FILE
    try:
        cache = json.loads(cache_path.read_text())
    except (OSError, ValueError):
        cache = {}
    if not isinstance(cache, dict):
        cache = {}
    if isinstance(cache.get(fingerprint), str):
        return cache[fingerprint]
    org_id = _fetch_active_org_id()
    if org_id is None:
        return None
    cache[fingerprint] = org_id
    try:
        from beakr_sdk.credentials import write_private_file

        write_private_file(cache_path, json.dumps(cache))
    except OSError:
        pass
    return org_id


def _fetch_active_org_id() -> str | None:
    """A key's or token's organization (``/me/active`` answers it for programmatic callers)."""
    from beakr_sdk.errors import BeakrError

    from beakr_cli.sdk import get_sdk

    try:
        with get_sdk(timeout=10.0) as sdk:
            org = sdk.me.active_org()
    except BeakrError:
        return None
    org_id = org.get("id") if isinstance(org, dict) else None
    return str(org_id) if org_id else None
