"""Auth headers sent to the Beakr API."""

from __future__ import annotations

from beakr_cli import __version__, client, config


def _no_stored_config(monkeypatch) -> None:
    monkeypatch.setattr(config, "get", lambda _key, default=None: default)
    for var in ("BEAKR_API_KEY", "BEAKR_ORG_ID", "BEAKR_DEV_DISPLAY_NAME"):
        monkeypatch.delenv(var, raising=False)


def test_dev_identity_name_is_forwarded(monkeypatch) -> None:
    """Without X-Identity-Name the API keys the dev user as "seed" and silently
    provisions a new user who belongs to nothing, so every scoped read is 403 or
    empty. The eval harness sets BEAKR_DEV_IDENTITY_NAME expecting it to be sent.
    """
    _no_stored_config(monkeypatch)
    monkeypatch.setenv("BEAKR_DEV_IDENTITY", "eval-user-id")
    monkeypatch.setenv("BEAKR_DEV_EMAIL", "eval@example.test")
    monkeypatch.setenv("BEAKR_DEV_IDENTITY_NAME", "eval-manifest")

    headers = client._build_headers()

    assert headers["X-Identity-Id"] == "eval-user-id"
    assert headers["X-Email"] == "eval@example.test"
    assert headers["X-Identity-Name"] == "eval-manifest"


def test_dev_identity_name_is_optional(monkeypatch) -> None:
    _no_stored_config(monkeypatch)
    monkeypatch.setenv("BEAKR_DEV_IDENTITY", "seed-user-id")
    monkeypatch.setenv("BEAKR_DEV_EMAIL", "seed@example.test")
    monkeypatch.delenv("BEAKR_DEV_IDENTITY_NAME", raising=False)

    assert "X-Identity-Name" not in client._build_headers()


def test_requests_identify_the_cli_version(monkeypatch) -> None:
    _no_stored_config(monkeypatch)
    monkeypatch.delenv("BEAKR_DEV_IDENTITY", raising=False)
    monkeypatch.setenv("BEAKR_API_KEY", "key")

    assert client._build_headers()["User-Agent"] == f"beakr-cli/{__version__}"
