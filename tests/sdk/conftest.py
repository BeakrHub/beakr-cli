"""Fixtures for the SDK tests that drive the CLI: a client pointed at a fake host that never sleeps.

The SDK's own tests live with the SDK (BeakrHub/beakr-python). The modules here
exercise SDK behavior through the CLI (login, export, local-mode results the
MCP server renders), so they stay with the CLI.
"""

from __future__ import annotations

import pytest
from beakr_sdk import AsyncBeakr, Beakr

from beakr_cli._private_api import AsyncPrivateBeakr, PrivateBeakr

BASE = "https://api.test"


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
def client(sleeps):
    beakr = Beakr(api_key="bk_live_test", base_url=BASE)
    beakr._sleep = sleeps.append
    yield beakr
    beakr.close()


@pytest.fixture
async def async_client(sleeps):
    beakr = AsyncBeakr(api_key="bk_live_test", base_url=BASE)

    async def record(seconds: float) -> None:
        sleeps.append(seconds)

    beakr._sleep = record
    yield beakr
    await beakr.close()


# The CLI's private clients (``beakr_cli._private_api``): the public ones plus
# the resources over unlisted routes, which the CLI and its MCP server use. Tests of
# those resources take these; a module about them only can rebind ``client``.


@pytest.fixture
def internal_client(sleeps):
    beakr = PrivateBeakr(api_key="bk_live_test", base_url=BASE)
    beakr._sleep = sleeps.append
    yield beakr
    beakr.close()


@pytest.fixture
async def async_internal_client(sleeps):
    beakr = AsyncPrivateBeakr(api_key="bk_live_test", base_url=BASE)

    async def record(seconds: float) -> None:
        sleeps.append(seconds)

    beakr._sleep = record
    yield beakr
    await beakr.close()
