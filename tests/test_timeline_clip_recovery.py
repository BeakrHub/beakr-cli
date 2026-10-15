from beakr_cli.mcp_render import render
from beakr_cli.mcp_result import finish


def envelope(text):
    return {
        "content": text,
        "coverage": {
            "command": "timeline",
            "returned": 100,
            "complete": False,
            "next_cursor": "unsafe-tail-cursor",
            "scanned": 100,
        },
        "next": ["Continue with cursor=unsafe-tail-cursor"],
    }


def test_clipped_timeline_requires_replay_not_forward_pagination():
    body = finish(
        envelope("Continue with cursor=unsafe-tail-cursor\n" + "x" * 60000), tool="knowledge_base"
    )
    visible = render("knowledge_base", body)
    assert "unsafe-tail-cursor" not in visible
    assert "same input cursor" in visible
    assert "include_content=false" in visible
    assert body["coverage"]["returned"] == 0
    assert body["coverage"]["complete"] is False
    assert body["upstream_coverage"]["next_cursor"] == "unsafe-tail-cursor"
    assert finish(body, tool="knowledge_base") == body


def test_unclipped_timeline_keeps_continuation():
    body = finish(envelope("Event evidence"), tool="knowledge_base")
    assert body["coverage"]["returned"] == 100
    assert "unsafe-tail-cursor" in render("knowledge_base", body)


def test_other_commands_keep_existing_clipping_contract():
    raw = envelope("x" * 60000)
    raw["coverage"]["command"] = "cat"
    body = finish(raw, tool="knowledge_base")
    assert body["coverage"]["truncated"] is True
    assert "upstream_coverage" not in body
