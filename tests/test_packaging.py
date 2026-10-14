"""Dependency bounds the published package must keep.

Regression: 0.2.1 declared ``mcp>=1.0``. ``mcp`` 2.x removed
``mcp.server.fastmcp``, so every fresh install resolved 2.x and ``beakr mcp``
died on import -- while a developer copy installed with a local ``mcp<2``
constraint kept working and hid it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on 3.10
    tomllib = pytest.importorskip("tomli")

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _requirement(name: str) -> Requirement:
    deps = tomllib.loads(PYPROJECT.read_text())["project"]["dependencies"]
    return next(req for req in map(Requirement, deps) if req.name == name)


def test_mcp_dependency_excludes_the_2x_line_the_server_cannot_import() -> None:
    spec = _requirement("mcp").specifier
    assert not spec.contains(Version("2.0.0"))
    # FastMCP tool annotations (used by beakr_version/update_beakr) need 1.9+.
    assert not spec.contains(Version("1.8.0"))
    assert spec.contains(Version("1.27.0"))


def test_package_version_matches_module_version() -> None:
    from beakr_cli import __version__

    assert tomllib.loads(PYPROJECT.read_text())["project"]["version"] == __version__
