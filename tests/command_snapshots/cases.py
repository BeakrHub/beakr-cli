"""The hand-written commands under snapshot, and the ones deliberately not.

Every visible hand-written command is either a case here or in ``EXEMPT`` with
the reason it cannot be snapshotted against a recorded API
(``test_every_hand_written_command_is_snapshotted_or_exempt`` enforces it). The
generated ``beakr api <area> <action>`` commands are not here: their requests
and output are proven against the real app, operation by operation, by
beakr-engine's ``tests/api/test_cli_web_action_fidelity.py``.
"""

from __future__ import annotations

import json

from tests.command_snapshots.runner import SnapshotCase

PROJECT = "4dd25154-0d80-44ab-b4c2-167319bfacb4"
ORG = "0b6f6a52-3a1e-4c55-9d1f-6e1b8f0a2c01"
PAGE = "Project Helios"
CARD = "Helios HX-07 Solubility Card"
EVIDENCE = "{{artifact:6e9422f6-04ea-41a2-87c1-d41ed1f62713-document-block-1}}"
ID = "5f0c2d1e-8a7b-4c3d-9e2f-1a0b9c8d7e6f"
ID2 = "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d"

TRANSCRIPT = (
    '{"type":"user","sessionId":"s-1","cwd":"/work/helios","timestamp":"2026-10-01T12:00:00Z",'
    '"message":{"role":"user","content":"Which lead did the committee pick?"}}\n'
    '{"type":"assistant","sessionId":"s-1","cwd":"/work/helios","timestamp":"2026-10-01T12:00:05Z",'
    '"message":{"role":"assistant","content":[{"type":"text","text":"NW-217."}]}}\n'
)


def c(name: str, *argv: str, **options) -> SnapshotCase:
    return SnapshotCase(name=name, argv=tuple(argv), **options)


# One command line per entry reads better than ruff's one-argument-per-line form.
# fmt: off
CASES: list[SnapshotCase] = [
    # --- identity and credentials ---------------------------------------------
    c("whoami", "whoami"),
    c("auth-whoami", "auth", "whoami"),
    c("auth-status", "auth", "status"),
    c("auth-set-scope", "auth", "set-scope", "--project", PROJECT),
    c("keys-ls", "keys", "ls"),
    c("keys-scopes", "keys", "scopes"),
    c("keys-revoke", "keys", "revoke", ID),
    c(
        "keys-create",
        "keys", "create", "--name", "ci", "--scope", "kb:read", "--project", PROJECT,
        "--no-browser",
    ),
    c("doctor", "doctor"),
    # --- org, members -----------------------------------------------------------
    c("org-show", "org", "show", "--org", ORG),
    c("org-credentials-ls", "org", "credentials", "ls", "--org", ORG),
    c(
        "org-credentials-revoke",
        "org", "credentials", "revoke", "api_key", ID, "--org", ORG, "--yes",
    ),
    c("org-service-accounts-ls", "org", "service-accounts", "ls", "--org", ORG),
    c("org-service-accounts-create", "org", "service-accounts", "create", "ci-bot", "--org", ORG),
    c(
        "org-service-accounts-disable",
        "org", "service-accounts", "disable", ID, "--org", ORG, "--yes",
    ),
    c(
        "org-service-accounts-key",
        "org", "service-accounts", "key", ID, "--scope", "kb:read", "--org", ORG,
    ),
    c("org-policy-show", "org", "policy", "show", "--org", ORG),
    c("org-policy-set", "org", "policy", "set", "--max-key-days", "90", "--org", ORG),
    c("members-ls", "members", "ls", "--org", ORG),
    c("members-invite", "members", "invite", "maja@northwind.example", "--role", "member"),
    c("members-role", "members", "role", ID, "admin", "--org", ORG),
    c("members-rm", "members", "rm", ID, "--org", ORG, "--yes"),
    c("members-invitations", "members", "invitations"),
    c("members-revoke-invite", "members", "revoke-invite", ID),
    c("usage", "usage", "--group-by", "day"),
    # --- projects and files -----------------------------------------------------
    c("projects-ls", "projects", "ls"),
    c("projects-create", "projects", "create", "Helios", "--description", "Lead selection"),
    c("projects-show", "projects", "show", "@GET /v1/projects#data.0.id"),
    c("projects-archive", "projects", "archive", "@GET /v1/projects#data.0.id"),
    c("projects-rm", "projects", "rm", "@GET /v1/projects#data.0.id", "--yes"),
    c("projects-members-ls", "projects", "members", "ls", "@GET /v1/projects#data.0.id"),
    c(
        "projects-members-add",
        "projects", "members", "add", "@GET /v1/projects#data.0.id", ID, "--role", "editor",
    ),
    c(
        "projects-members-rm",
        "projects", "members", "rm", "@GET /v1/projects#data.0.id", ID, "--yes",
    ),
    c(
        "projects-members-invite",
        "projects", "members", "invite", "@GET /v1/projects#data.0.id", "maja@northwind.example",
    ),
    c("workspace-projects", "workspace", "projects"),
    c("workspace-profiles", "workspace", "profiles"),
    c("init", "init", "--project", "@GET /v1/projects#data.0.id"),
    c("files-ls", "files", "ls", "--project", PROJECT),
    c("files-show", "files", "show", ID),
    c("files-download", "files", "download", ID, "--out", "charter.pdf"),
    c("files-rm", "files", "rm", ID, "--yes"),
    c("files-versions", "files", "versions", ID),
    c(
        "upload",
        "upload", "charter.md", "--project", PROJECT,
        files={"charter.md": "# Helios charter\n\nFind a thermostable lead.\n"},
    ),
    # --- Knowledge ------------------------------------------------------------------
    c("kb-ls", "kb", "ls", "--project", PROJECT),
    c("kb-cat", "kb", "cat", PAGE, "--project", PROJECT),
    # The revisions list has no text: the revision's text is read from as-of.
    c(
        "kb-cat-rev",
        "kb", "cat", PAGE, "--rev", "6", "--project", PROJECT,
        variants={"POST /v1/knowledge/wiki/command": "cat_rev"},
    ),
    c("kb-search", "kb", "search", "thermostable lead", "--project", PROJECT),
    c("kb-grep", "kb", "grep", "NW-217", "--project", PROJECT),
    c("kb-sections", "kb", "sections", "Helios lead", "--project", PROJECT),
    c("kb-blame", "kb", "blame", PAGE, "--project", PROJECT),
    c("kb-log", "kb", "log", PAGE, "--project", PROJECT),
    c("kb-sources", "kb", "sources", PAGE, "--project", PROJECT),
    c("kb-provenance", "kb", "provenance", PAGE, "--project", PROJECT),
    c("kb-links", "kb", "links", PAGE, "--project", PROJECT),
    c("kb-timeline", "kb", "timeline", "--project", PROJECT),
    c("kb-graph", "kb", "graph", "--project", PROJECT),
    c("kb-stats", "kb", "stats", "--project", PROJECT),
    c("kb-research", "kb", "research", "Which lead was selected?", "--project", PROJECT),
    c("research", "research", "Which lead was selected?", "--project", PROJECT),
    # The Knowledge door cases send the requests recorded in
    # tests/fixtures/knowledge_door_results.json, so request and answer agree.
    c("kb-rows", "kb", "rows", "--table", "facts", "--scope", PROJECT),
    c(
        "kb-add-fact",
        "kb", "add-fact", CARD, "--attribute", "Operator", "--value", "Kim",
        "--when", "2026-05-02", "--evidence", EVIDENCE,
        "--rationale", "Shape parity check for add_fact.", "--scope", PROJECT,
    ),
    c(
        "kb-add-connection",
        "kb", "add-connection", CARD, "--relation", "Follows",
        "--to", "Protocol: Thermal Shift Assay", "--evidence", EVIDENCE,
        "--rationale", "Shape parity check for add_connection.", "--scope", PROJECT,
        variants={"POST /v1/knowledge/wiki/write": "add_connection"},
    ),
    c(
        "kb-add-finding",
        "kb", "add-finding", CARD, "--date", "2026-06-20", "--kind", "decision",
        "--text", "Report the screen under HX-01.", "--evidence", EVIDENCE,
        "--rationale", "Shape parity check for add_finding.", "--scope", PROJECT,
        variants={"POST /v1/knowledge/wiki/write": "add_finding"},
    ),
    c(
        "kb-edit-rows",
        "kb", "edit-rows", CARD, "--table", "facts",
        "--old-row", '{"attribute": "Plates run", "value": "2"}',
        "--row", '{"attribute": "Plates run", "value": "3"}',
        "--reason", "The plate log shows a third plate.",
        "--rationale", "Shape parity check for edit_rows.", "--scope", PROJECT,
        variants={"POST /v1/knowledge/wiki/write": "edit_rows"},
    ),
    c("kb-entities", "kb", "entities", "--project", PROJECT),
    c("kb-history", "kb", "history", PAGE, "--project", PROJECT),
    c("kb-conflicts", "kb", "conflicts", "--project", PROJECT),
    c("kb-undo-run", "kb", "undo-run", "run-2026-10-01", "--yes"),
    c(
        "kb-propose-create",
        "kb", "propose", "create", "--project", PROJECT, "--title", "Helios Budget",
        "--content", "Fiscal 2024 funding is USD 2.4 million.",
    ),
    c(
        "kb-propose-edit",
        "kb", "propose", "edit", PAGE, "--project", PROJECT,
        "--content", "The committee approved NW-217.",
    ),
    c(
        "kb-propose-replace",
        "kb", "propose", "replace", "--find", "NW-205", "--replace", "NW-217",
        "--project", PROJECT,
    ),
    c("kb-propose-move", "kb", "propose", "move", PAGE, "--title", "Helios", "--project", PROJECT),
    c("kb-propose-archive", "kb", "propose", "archive", PAGE, "--project", PROJECT),
    c("kb-proposals-ls", "kb", "proposals", "ls", "--project", PROJECT),
    c("kb-proposals-show", "kb", "proposals", "show", ID),
    c("kb-proposals-accept", "kb", "proposals", "accept", ID, "--yes"),
    c("kb-proposals-dismiss", "kb", "proposals", "dismiss", ID, "--yes"),
    # --- agent context and conversations ---------------------------------------
    c("context", "context", "--project", PROJECT),
    c("context-claude-md", "context", "--project", PROJECT, "--format", "claude_md"),
    c("compound", "compound", ID, "--project", PROJECT),
    c("changes", "changes", "--project", PROJECT),
    c("trace", "trace", "page", ID),
    c("conversations-ls", "conversations", "ls", "--project", PROJECT),
    c("conversations-show", "conversations", "show", ID),
    c("conversations-search", "conversations", "search", "lead selection", "--project", PROJECT),
    c("conversations-freshness", "conversations", "freshness", ID),
    c(
        "conversations-import",
        "conversations", "import", "--from-transcript", "session.jsonl", "--project", PROJECT,
        files={"session.jsonl": TRANSCRIPT},
    ),
    c("conversations-full-text", "conversations", "full-text"),
    c("conversations-rm", "conversations", "rm", ID, "--yes"),
    c("profile-show", "profile", "show"),
    c("profile-create", "profile", "create", "--project", PROJECT, "--goal", "Pick a lead"),
    c("profile-confirm", "profile", "confirm", ID),
    c("profile-dismiss", "profile", "dismiss", ID),
    c("profile-edit", "profile", "edit", ID, "--content", "Lead is NW-217"),
    c(
        "profile-apply",
        "profile", "apply", "--file", "profile.json",
        files={
            "profile.json": json.dumps(
                {
                    "changes": [
                        {"claim_id": ID, "action": "confirm"},
                        {"claim_id": ID2, "action": "dismiss"},
                    ]
                }
            )
        },
    ),
    c("profile-forget", "profile", "forget", ID, "--yes"),
    c("profile-restore", "profile", "restore", ID),
    c("memory-ls", "memory", "ls"),
    c("memory-rm", "memory", "rm", ID, "--yes"),
    # --- agent runs, workflows, outputs, interviews ----------------------------
    c("ask", "ask", "Which lead did the committee pick?", "--project", PROJECT),
    c("runs-ls", "runs", "ls"),
    c("runs-show", "runs", "show", ID),
    c("runs-cancel", "runs", "cancel", ID),
    c("workflows-ls", "workflows", "ls"),
    c("workflows-create", "workflows", "create", ID, "--name", "Weekly Helios digest"),
    c("workflows-draft", "workflows", "draft", "Summarize Helios every Monday"),
    c("workflows-run", "workflows", "run", ID),
    c("workflows-schedule", "workflows", "schedule", ID, "0 9 * * 1"),
    c("workflows-runs", "workflows", "runs", ID),
    c("outputs-ls", "outputs", "ls", "--project", PROJECT),
    c("outputs-show", "outputs", "show", ID),
    c("outputs-export", "outputs", "export", ID, "--out", "helios.md"),
    c("outputs-history", "outputs", "history", ID),
    c(
        "interviews-request",
        "interviews", "request", "Helios retro", "--question", "What slowed us?",
    ),
    c("interviews-draft", "interviews", "draft", "Ask the team what slowed Helios"),
    c("interviews-ls", "interviews", "ls"),
    c("interviews-show", "interviews", "show", ID),
    # --- skills, integrations, webhooks ---------------------------------------
    c("skills-ls", "skills", "ls"),
    c("skills-show", "skills", "show", "@GET /v1/me/skills/library#data.0.name"),
    c("skills-proposals", "skills", "proposals"),
    c("skills-approve", "skills", "approve", ID),
    c(
        "skills-edit",
        "skills", "edit", "@GET /v1/me/skills/library#data.0.name", "--file", "SKILL.md",
        files={"SKILL.md": "---\nname: helios-digest\ndescription: Weekly digest\n---\n\nSteps.\n"},
    ),
    c("integrations-ls", "integrations", "ls"),
    c("integrations-connect", "integrations", "connect", "google_drive", "--no-browser"),
    c("integrations-status", "integrations", "status", ID),
    c("integrations-coverage", "integrations", "coverage", ID),
    c("integrations-sync", "integrations", "sync", ID),
    c("integrations-rm", "integrations", "rm", ID, "--yes"),
    c("integrations-disconnect", "integrations", "disconnect", ID, "--yes"),
    c("integrations-scope", "integrations", "scope", ID),
    # --- background jobs -------------------------------------------------------
    c("jobs-show", "jobs", "show", "7d9f1b3c-5e7a-4c9e-8b2d-4f6a8c0e2b57"),
    c("jobs-wait", "jobs", "wait", "7d9f1b3c-5e7a-4c9e-8b2d-4f6a8c0e2b57"),
]
# fmt: on

_NO_EXTERNAL_USERS_FIXTURE = (
    "no recorded external-users response yet; tests/test_cli_agent_friction.py covers it"
)
_NO_LABELS_FIXTURE = (
    "no recorded /v1/labels response yet; tests/test_cli_agent_friction.py covers it"
)

#: Visible hand-written commands with no snapshot, each with the reason.
EXEMPT: dict[str, str] = {
    "external-users ls": _NO_EXTERNAL_USERS_FIXTURE,
    "external-users create": _NO_EXTERNAL_USERS_FIXTURE,
    "external-users show": _NO_EXTERNAL_USERS_FIXTURE,
    "external-users edit": _NO_EXTERNAL_USERS_FIXTURE,
    "external-users rm": _NO_EXTERNAL_USERS_FIXTURE,
    "labels ls": _NO_LABELS_FIXTURE,
    "labels create": _NO_LABELS_FIXTURE,
    "labels show": _NO_LABELS_FIXTURE,
    "labels edit": _NO_LABELS_FIXTURE,
    "labels rm": _NO_LABELS_FIXTURE,
    "labels members ls": _NO_LABELS_FIXTURE,
    "labels members add": _NO_LABELS_FIXTURE,
    "labels members rm": _NO_LABELS_FIXTURE,
    "labels projects ls": _NO_LABELS_FIXTURE,
    "labels projects add": _NO_LABELS_FIXTURE,
    "labels projects rm": _NO_LABELS_FIXTURE,
    "mcp": "stdout carries the MCP stdio protocol; tests/test_mcp_*.py cover its tools",
    "codex": "passes its arguments to the codex binary; tests/test_codex_wrapper_and_uninstall.py",
    "version": "reports PyPI release state, not an API response; tests/test_updates.py",
    "update": "upgrades the installed package via uv/pipx; tests/test_updates.py",
    "install": "writes local agent config only, no API call; tests/test_setup_auth.py",
    "setup": "writes local agent config only, no API call; tests/test_setup_auth.py",
    "auth login": "interactive device-code sign-in in a browser; tests/test_cli_auth_device.py",
    "auth logout": "revokes the stored login's tokens; tests/test_auth_cli.py",
    "auth dev": "writes a local dev identity, no API response rendered; tests/test_auth_cli.py",
    "auth set-org": "local config write only; tests/test_cli_json_everywhere.py",
    "auth clear-org": "local config write only; tests/test_cli_json_everywhere.py",
    "auth token": "credential helper printing a secret; tests/test_cli_auth_token.py",
    "hooks install": "writes local agent hook config; tests/test_cli_hooks.py",
    "hooks uninstall": "removes local agent hook config; tests/test_cli_hooks.py",
    "hooks status": "reads local agent hook config; tests/test_cli_hooks.py",
    "hooks capture": "local consent switch; tests/test_hook_capture_consent.py",
    "hooks learning": "local consent switch; tests/test_hook_capture_consent.py",
    "skills sync": "syncs skill files on disk in both directions; tests/test_skills_sync*.py",
    # Commands over operations added to the documented core on 2026-10-12, before
    # the engine recorded positive-path responses for them; their requests and
    # output are pinned with mocked responses in tests/test_cli_core_surface.py.
    "files upload": "the `upload` command registered under `files` too (snapshot `upload`)",
    "end-user-tokens mint": "no recorded response yet; tests/test_cli_core_surface.py",
    "end-user-tokens ls": "no recorded response yet; tests/test_cli_core_surface.py",
    "end-user-tokens revoke": "no recorded response yet; tests/test_cli_core_surface.py",
    "integrations tools": "no recorded response yet; tests/test_cli_core_surface.py",
    "integrations call": "no recorded response yet; tests/test_cli_core_surface.py",
    "interviews link create": "no recorded response yet; tests/test_cli_core_surface.py",
    "interviews link ls": "no recorded response yet; tests/test_cli_core_surface.py",
    "interviews link show": "no recorded response yet; tests/test_cli_core_surface.py",
    "interviews link send": "no recorded response yet; tests/test_cli_core_surface.py",
    "interviews link revoke": "no recorded response yet; tests/test_cli_core_surface.py",
}
