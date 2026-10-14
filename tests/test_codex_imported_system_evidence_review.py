"""System task reports in a human session must not become the person's evidence."""

import json

from beakr_cli.transcripts import HUMAN, parse_claude_code_transcript


def test_system_task_notification_is_not_human_learning_evidence(tmp_path):
    human = "Please check the build results for this project."
    automated = "I am migrating the billing database to a new ledger schema this month."
    report = f"<task-notification><result>{automated}</result></task-notification>"
    common = {
        "type": "user",
        "entrypoint": "cli",
        "sessionId": "mixed-human-and-worker",
        "isMeta": False,
        "isSidechain": False,
    }
    records = [
        {
            **common,
            "promptSource": "typed",
            "origin": {"kind": "human"},
            "message": {"role": "user", "content": human},
        },
        {
            **common,
            "promptSource": "system",
            "origin": {"kind": "task-notification"},
            "message": {"role": "user", "content": report},
        },
    ]
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("\n".join(json.dumps(row) for row in records) + "\n")
    parsed = parse_claude_code_transcript(transcript)
    user_texts = [m["content"] for m in parsed.messages if m["role"] == "user"]
    assert human in user_texts
    assert not (parsed.classification == HUMAN and report in user_texts), (
        "The parser marks this session human but strips the system origin from a worker report, "
        "making its text eligible as the person's own quoted learning evidence."
    )
