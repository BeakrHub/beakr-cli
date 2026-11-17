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


def test_mcp_dependency_is_the_2x_line_that_speaks_2026_07_28() -> None:
    # `uv tool install` ignores uv.lock, so the specifier alone decides what a
    # fresh install gets. The server is built on mcp 2's MCPServer
    # (mcp.server.mcpserver); mcp 1 has no such module and stops at protocol
    # 2025-11-25, so a 2026-07-28 client paid a refused server/discover probe and
    # a fallback to initialize on every connection. 2.3 is the first release
    # this server was built and tested against.
    spec = _requirement("mcp").specifier
    assert not spec.contains(Version("1.27.0"))
    assert not spec.contains(Version("2.2.0"))
    assert spec.contains(Version("2.3.0"))
    assert not spec.contains(Version("3.0.0"))


def test_typer_dependency_excludes_the_line_that_vendors_its_own_click() -> None:
    # Regression: the bound was `typer>=0.15`, and `uv tool install` ignores
    # uv.lock, so a fresh install resolved typer 0.27. From 0.26 typer ships its
    # own click (`typer._click`); its error handler stops recognising the real
    # click exceptions `beakr api` raises, and every usage error -- even
    # `beakr api --help` -- printed a traceback (found on a fresh install,
    # 2026-10-25). 0.26.x and 0.27.x both break.
    spec = _requirement("typer").specifier
    assert not spec.contains(Version("0.26.0"))
    assert not spec.contains(Version("0.27.3"))
    assert spec.contains(Version("0.25.0"))


def test_package_version_matches_module_version() -> None:
    from beakr_cli import __version__

    assert tomllib.loads(PYPROJECT.read_text())["project"]["version"] == __version__
