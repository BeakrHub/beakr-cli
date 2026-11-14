"""``source_records.with_read_call`` against both SDK shapes of a public source record.

The CLI pins ``beakr-sdk >=0.2.0,<0.3``. ``public_source`` in 0.2.x drops a
connected item's ``retrieval`` unless it is a file download handle, so the CLI
adds the read call back (``beakr_cli/source_records.py``, the only place that
does). 0.3.0 forwards it itself; there the workaround must change nothing for a
well-formed call, so the CLI can move to 0.3 by bumping the pin alone. The 0.3
shape is the SDK checkout's own ``public_source`` (``BEAKR_SDK_PATH`` or the
sibling ``beakr-python-rf3`` worktree), loaded from source; without one the test
skips.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import ModuleType

import pytest
from beakr_sdk.sources import public_source as installed_public_source

from beakr_cli.source_records import with_read_call

SLACK = {
    "name": "Slack: #lightfield-beakr",
    "provider": "slack",
    "location": {"url": "https://slack.com/app_redirect?channel=C0AMUSP0YMB"},
    "retrieval": {"tool": "slack_list_messages", "arguments": {"channel_id": "C0AMUSP0YMB"}},
}
FILE = {
    "name": "memo.pdf",
    "retrieval": {"tool": "gdrive_download", "arguments": {"handle": "h-1"}},
}
HOSTILE = {
    "name": "x",
    "retrieval": {"tool": "slack_list_messages'; rm", "arguments": {"channel_id": "C1"}},
}


def _sdk_0_3() -> ModuleType:
    candidates = [os.environ.get("BEAKR_SDK_PATH")]
    candidates.append(str(Path(__file__).resolve().parents[2] / "beakr-python-rf3"))
    for root in filter(None, candidates):
        sources = Path(root) / "src" / "beakr_sdk" / "sources.py"
        if sources.exists() and 'version = "0.2' not in (Path(root) / "pyproject.toml").read_text():
            spec = importlib.util.spec_from_file_location("_beakr_sdk_sources_0_3", sources)
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    pytest.skip("needs a beakr-python checkout at 0.3.0 or later (BEAKR_SDK_PATH)")
    raise AssertionError("unreachable")


def test_with_the_installed_sdk_the_read_call_is_added_back():
    record = installed_public_source(SLACK)
    assert "retrieval" not in record  # 0.2.x drops it: what the workaround is for

    assert with_read_call(record, SLACK)["retrieval"] == SLACK["retrieval"]


def test_with_sdk_0_3_a_well_formed_record_is_left_as_the_sdk_built_it():
    public_source = _sdk_0_3().public_source
    for source in (SLACK, FILE):
        record = public_source(source)
        before = dict(record)

        assert with_read_call(record, source) == before


def test_a_read_call_whose_names_are_not_plain_is_never_kept_either_way():
    assert "retrieval" not in with_read_call(installed_public_source(HOSTILE), HOSTILE)
    record = _sdk_0_3().public_source(HOSTILE)
    assert "retrieval" in record  # 0.3.0 forwards it
    assert "retrieval" not in with_read_call(record, HOSTILE)
