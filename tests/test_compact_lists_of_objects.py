"""Piped output never puts a list of objects in one column as JSON.

Root cause: ``compact._every_field`` (how an object with neither an id nor a
name is printed piped: every field as ``key=value``) wrote a list of objects as
``key=<the list as JSON>``, so ``conversations freshness``, ``integrations
coverage``, ``interviews ls``, ``kb graph``, ``kb provenance``, ``kb undo-run``,
``profile apply`` and ``workflows draft`` printed a JSON array inside a
tab-separated line. Each object of such a list is now a line of its own, led by
the field's name, with every field as ``key=value``; ``interviews ls`` prints
one line per interview, session and request, as its tables do.
"""

from __future__ import annotations

import re

import pytest

from beakr_cli.compact import summary_line
from tests.command_snapshots.cases import CASES

AFFECTED = (
    "conversations-freshness",
    "integrations-coverage",
    "interviews-ls",
    "kb-graph",
    "kb-provenance",
    "kb-undo-run",
    "profile-apply",
    "workflows-draft",
)


def test_a_list_of_objects_is_one_line_per_object_not_json_in_a_column():
    line = summary_line(
        {
            "fresh": False,
            "changed": [
                {"page_id": "p1", "title": "Decision", "state": "changed"},
                {"page_id": "p2", "title": "Charter", "sections": [{"section_id": "s1"}]},
            ],
            "edges": [{"from": "p1", "to": "p2"}],
        }
    )

    assert line.split("\n") == [
        "fresh=false",
        "changed\tpage_id=p1\ttitle=Decision\tstate=changed",
        "changed\tpage_id=p2\ttitle=Charter",
        "changed\tsections\tsection_id=s1",
        "edges\tfrom=p1\tto=p2",
    ]


def _piped_stdout(name: str) -> str:
    case = next(case for case in CASES if case.name == name)
    runs = re.split(r"\n(?=\$ beakr )", case.snapshot.read_text())
    piped = runs[case.modes.index("piped")]
    return piped.split("--- stdout\n", 1)[1].split("--- stderr", 1)[0]


@pytest.mark.parametrize("name", AFFECTED)
def test_piped_output_has_no_json_in_a_column(name):
    stdout = _piped_stdout(name)
    assert stdout.strip()
    for line in stdout.splitlines():
        for column in line.split("\t"):
            assert not re.match(r"(\S+=)?[\[{]\s*[\"{\[]", column), (name, column)
