"""The CLI, MCP server and hooks are Apache-2.0 (developer platform W11.1).

The SDKs (BeakrHub/beakr-python, BeakrHub/beakr-typescript) carry the same license.

Root cause this guards: the repository LICENSE and the Python package metadata
said MIT while the specification and the TypeScript package said Apache-2.0.
"""

from __future__ import annotations

from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[1]


def test_the_repository_license_is_apache_2():
    text = (ROOT / "LICENSE").read_text()
    assert "Apache License" in text and "Version 2.0, January 2004" in text
    assert "Copyright 2026 Beakr" in text
    assert "{yyyy}" not in text


def test_the_python_package_declares_apache_2_only():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert project["license"] == "Apache-2.0"
    assert not any("License ::" in c for c in project.get("classifiers", []))
