from beakr_cli import client


def test_dev_identity_name_environment_is_forwarded(monkeypatch):
    monkeypatch.setenv("BEAKR_DEV_IDENTITY", "eval-user-id")
    monkeypatch.setenv("BEAKR_DEV_EMAIL", "eval-user@example.test")
    monkeypatch.setenv("BEAKR_DEV_IDENTITY_NAME", "eval-manifest")
    monkeypatch.delenv("BEAKR_API_KEY", raising=False)

    headers = client._build_headers()

    assert headers["X-Identity-Id"] == "eval-user-id"
    assert headers["X-Identity-Name"] == "eval-manifest"
    assert headers["X-Email"] == "eval-user@example.test"


def test_dev_identity_name_remains_optional(monkeypatch):
    monkeypatch.setenv("BEAKR_DEV_IDENTITY", "seed-user-id")
    monkeypatch.setenv("BEAKR_DEV_EMAIL", "seed@example.test")
    monkeypatch.delenv("BEAKR_DEV_IDENTITY_NAME", raising=False)
    monkeypatch.delenv("BEAKR_API_KEY", raising=False)

    headers = client._build_headers()

    assert "X-Identity-Name" not in headers
