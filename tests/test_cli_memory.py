"""``beakr context``, ``beakr compound`` and ``beakr conversations``."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from beakr_cli.hook_runtime import hooks_log_path, read_state
from beakr_cli.main import app
from beakr_cli.project_binding import write_binding
from tests.hook_keys import hook_key

# These tests are about what capture sends, not whether a repository consents.
pytestmark = pytest.mark.usefixtures("capture_consented")

API = "https://api.test"
FIXTURES = Path(__file__).parent / "fixtures" / "transcripts"
CLAUDE_FIXTURE = FIXTURES / "claude_code_session.jsonl"


@pytest.fixture(autouse=True)
def api_env(monkeypatch):
    monkeypatch.setenv("BEAKR_API_URL", API)
    monkeypatch.setenv("BEAKR_API_KEY", "bk_live_test")


def _body(route: respx.Route, index: int = -1) -> dict:
    return json.loads(route.calls[index].request.content)


# ---------------------------------------------------------------------------
# context
# ---------------------------------------------------------------------------


@respx.mock
def test_context_hook_emits_session_start_additional_context() -> None:
    route = respx.post(f"{API}/v1/context").mock(
        return_value=httpx.Response(200, json={"format": "claude_md", "content": "# Org\nFacts."})
    )

    result = CliRunner().invoke(
        app, ["context", "--format", "claude_md", "--project", "auto", "--hook"], input="{}"
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    output = payload["hookSpecificOutput"]
    assert output["hookEventName"] == "SessionStart"
    assert output["additionalContext"].startswith("# Org\nFacts.")
    assert "session capture is on" in output["additionalContext"]
    request = route.calls[0].request
    assert request.headers["X-Beakr-Client"] == "claude-code-hook"
    assert _body(route) == {"format": "claude_md"}


@respx.mock
def test_context_hook_uses_the_binding_of_the_session_directory(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    write_binding(repo, {"project_id": "proj-bound"})
    route = respx.post(f"{API}/v1/context").mock(
        return_value=httpx.Response(200, json={"content": "ctx"})
    )

    result = CliRunner().invoke(
        app,
        ["context", "--format", "claude_md", "--hook"],
        input=json.dumps({"cwd": str(repo), "hook_event_name": "SessionStart"}),
    )

    assert result.exit_code == 0
    assert _body(route)["project_id"] == "proj-bound"


@respx.mock
def test_context_hook_failure_is_silent_and_logged() -> None:
    respx.post(f"{API}/v1/context").mock(return_value=httpx.Response(500, json={"detail": "boom"}))

    result = CliRunner().invoke(app, ["context", "--format", "claude_md", "--hook"], input="")

    assert result.exit_code == 0
    assert result.stdout == ""
    assert "context hook failed" in hooks_log_path().read_text()


def test_context_hook_without_credentials_still_exits_zero(monkeypatch) -> None:
    monkeypatch.delenv("BEAKR_API_KEY")

    result = CliRunner().invoke(app, ["context", "--hook"], input="")

    assert result.exit_code == 0
    assert "context hook failed" in hooks_log_path().read_text()


@respx.mock
def test_context_prints_content_and_writes_out_file(tmp_path: Path) -> None:
    respx.post(f"{API}/v1/context").mock(
        return_value=httpx.Response(200, json={"content": "## Memories\n- a"})
    )

    printed = CliRunner().invoke(app, ["context", "--project", "p1"])
    assert printed.exit_code == 0
    assert printed.stdout == "## Memories\n- a\n"

    out = tmp_path / "ctx.md"
    written = CliRunner().invoke(app, ["context", "--out", str(out)])
    assert written.exit_code == 0
    assert out.read_text() == "## Memories\n- a"


# Root cause: --out wrote result["content"] (the rendered markdown) for every
# format, so `--format json --out ctx.json` produced a markdown file. A JSON
# file now holds the response JSON, the same thing stdout prints.
@respx.mock
def test_context_format_json_out_writes_the_json_response(tmp_path: Path) -> None:
    response = {"content": "## Memories\n- a", "sections": [{"kind": "memories"}]}
    respx.post(f"{API}/v1/context").mock(return_value=httpx.Response(200, json=response))

    out = tmp_path / "ctx.json"
    written = CliRunner().invoke(app, ["context", "--format", "json", "--out", str(out)])

    assert written.exit_code == 0, written.output
    assert json.loads(out.read_text()) == response


def test_context_rejects_unknown_format() -> None:
    result = CliRunner().invoke(app, ["context", "--format", "yaml"])
    assert result.exit_code == 2


# ---------------------------------------------------------------------------
# compound
# ---------------------------------------------------------------------------


def _mock_import_and_compound() -> tuple[respx.Route, respx.Route]:
    imported = respx.post(f"{API}/v1/conversations/import").mock(
        return_value=httpx.Response(
            200, json={"conversation_id": "conv-1", "created": True, "stored": "summary"}
        )
    )
    compounded = respx.post(f"{API}/v1/compound").mock(
        return_value=httpx.Response(202, json={"status": "queued", "conversation_id": "conv-1"})
    )
    return imported, compounded


@respx.mock
def test_compound_from_transcript_imports_then_compounds() -> None:
    imported, compounded = _mock_import_and_compound()

    result = CliRunner().invoke(
        app,
        ["compound", "--from-transcript", str(CLAUDE_FIXTURE), "--client", "claude-code", "--json"],
    )

    assert result.exit_code == 0, result.output
    body = _body(imported)
    assert body == {
        "client": "claude-code",
        "session_id": "cc-session-1",
        "base_index": 0,
        "messages": body["messages"],
        "files_touched": ["/work/repo/billing.py", "/work/repo/tests/test_billing.py"],
        "title": "Refactor the billing client to retry on 503.",
        # The fixture carries no host markers (unknown) and its directory is
        # not opted in to learning, so it is never sent as interactive.
        "interactive": False,
        # The tools the session called, counted from its transcript (PROSE-392).
        "tools_used": [
            {"name": "Edit", "count": 1},
            {"name": "Read", "count": 1},
            {"name": "Write", "count": 1},
        ],
        # Never full text by default (C084/C086): store_full_text is left out,
        # so only the person's own per-client setting could keep the text. The
        # text is held, bounded by
        # the engine's deadline, for the capture this one request queues.
        "hold_text": True,
        "compound": True,
    }
    assert len(body["messages"]) == 3
    assert "Idempotency-Key" in imported.calls[0].request.headers
    assert _body(compounded) == {"conversation_id": "conv-1"}
    assert json.loads(result.stdout)["compounded"] is True


def _growing_transcript(tmp_path: Path) -> Path:
    path = tmp_path / "session.jsonl"
    shutil.copy(CLAUDE_FIXTURE, path)
    return path


def _append_turn(path: Path, text: str) -> None:
    line = {
        "type": "user",
        "sessionId": "cc-session-1",
        "cwd": "/work/repo",
        "timestamp": "2026-10-08T11:00:00Z",
        "message": {"role": "user", "content": text},
    }
    with path.open("a") as handle:
        handle.write(json.dumps(line) + "\n")


@respx.mock
def test_stop_hook_sends_only_new_messages_and_throttles_compounding(tmp_path: Path) -> None:
    """Each turn sends only the messages after the session cursor, positioned with
    ``base_index``. Regression: the tail used to be sent without ``base_index``,
    which the engine reads as the whole transcript from position 0, so every
    incremental send replaced the stored history with just the tail."""
    imported, compounded = _mock_import_and_compound()
    transcript = _growing_transcript(tmp_path)
    args = ["compound", "--from-transcript", str(transcript), "--hook"]

    assert CliRunner().invoke(app, args, input="").exit_code == 0
    assert len(_body(imported)["messages"]) == 3
    assert compounded.call_count == 1

    # A turn with nothing new sends nothing at all.
    assert CliRunner().invoke(app, args, input="").exit_code == 0
    assert imported.call_count == 1

    _append_turn(transcript, "Also cover 504.")
    assert CliRunner().invoke(app, args, input="").exit_code == 0
    assert imported.call_count == 2
    assert [m["content"] for m in _body(imported)["messages"]] == ["Also cover 504."]
    assert json.loads(imported.calls[-1].request.content)["base_index"] == 3
    assert compounded.call_count == 1  # throttled

    # SessionEnd forces the compound of what the throttle held back.
    assert CliRunner().invoke(app, [*args, "--force"], input="").exit_code == 0
    assert compounded.call_count == 2
    state = read_state("hooks_state.json")[hook_key(API)]
    assert state["cursor"] == 4
    assert state["compounded_count"] == 4

    # Nothing new since the last compound: even --force does not re-run it.
    assert CliRunner().invoke(app, [*args, "--force"], input="").exit_code == 0
    assert compounded.call_count == 2


@respx.mock
def test_failed_hook_import_is_queued_then_sent_first(tmp_path: Path) -> None:
    from beakr_cli import hook_queue

    transcript = _growing_transcript(tmp_path)
    args = ["compound", "--from-transcript", str(transcript), "--hook"]
    down = respx.post(f"{API}/v1/conversations/import").mock(
        side_effect=httpx.ConnectError("offline")
    )
    respx.post(f"{API}/v1/compound").respond(202, json={"status": "queued"})

    assert CliRunner().invoke(app, args, input="").exit_code == 0
    assert len(hook_queue.queued_files()) == 1
    assert down.called

    _append_turn(transcript, "Next turn.")
    up = respx.post(f"{API}/v1/conversations/import").respond(
        200, json={"conversation_id": "conv-1", "created": False, "stored": "summary"}
    )
    assert CliRunner().invoke(app, args, input="").exit_code == 0
    # The import is a replaying create, so the SDK made three attempts while
    # offline (the hook runs detached, so the agent never waits on them); then
    # the queued batch goes first and only the turn added since follows.
    sizes = [len(json.loads(c.request.content)["messages"]) for c in up.calls]
    assert sizes == [3, 3, 3, 3, 1]
    # The queued batch keeps its position; the next turn starts after it.
    assert [json.loads(c.request.content)["base_index"] for c in up.calls] == [0, 0, 0, 0, 3]
    assert hook_queue.queued_files() == []


def test_queue_is_bounded_and_full_queue_keeps_the_cursor(tmp_path: Path, monkeypatch) -> None:
    from beakr_cli import hook_queue

    monkeypatch.setattr(hook_queue, "MAX_QUEUED", 1)
    transcript = _growing_transcript(tmp_path)
    args = ["compound", "--from-transcript", str(transcript), "--hook"]
    with respx.mock:
        respx.post(f"{API}/v1/conversations/import").mock(side_effect=httpx.ConnectError("x"))
        assert CliRunner().invoke(app, args, input="").exit_code == 0
        _append_turn(transcript, "More.")
        assert CliRunner().invoke(app, args, input="").exit_code == 0
    assert len(hook_queue.queued_files()) == 1
    # The second batch was not queued, so the cursor stays put and the
    # transcript re-supplies those messages once the queue drains.
    assert read_state("hooks_state.json")[hook_key(API)]["cursor"] == 3
    assert not hook_queue.queue_health().healthy


def test_hooks_do_nothing_when_nested_inside_another_hook(monkeypatch) -> None:
    monkeypatch.setenv("BEAKR_HOOK_ACTIVE", "1")
    with respx.mock(assert_all_called=False) as router:
        compound = CliRunner().invoke(
            app, ["compound", "--hook", "--from-transcript", str(CLAUDE_FIXTURE)], input=""
        )
        context = CliRunner().invoke(app, ["context", "--hook"], input="{}")
        assert not router.calls
    assert compound.exit_code == 0 and context.exit_code == 0
    assert context.stdout == ""


@respx.mock
def test_hook_reads_transcript_path_from_stdin() -> None:
    imported, _ = _mock_import_and_compound()
    stdin = json.dumps({"session_id": "x", "transcript_path": str(CLAUDE_FIXTURE)})

    result = CliRunner().invoke(app, ["compound", "--hook", "--client", "claude-code"], input=stdin)

    assert result.exit_code == 0
    assert imported.call_count == 1
    assert imported.calls[0].request.headers["X-Beakr-Client"] == "claude-code-hook"


@respx.mock
def test_hook_failure_is_silent() -> None:
    respx.post(f"{API}/v1/conversations/import").mock(return_value=httpx.Response(400))

    result = CliRunner().invoke(
        app, ["compound", "--hook", "--from-transcript", str(CLAUDE_FIXTURE)], input=""
    )

    assert result.exit_code == 0
    assert "compound hook failed" in hooks_log_path().read_text()


def test_background_detaches_with_the_stdin_path_made_explicit(monkeypatch) -> None:
    spawned: list[list[str]] = []

    def fake_popen(args, **kwargs):
        assert kwargs["start_new_session"] is True
        # Everything the hook starts is marked, so nested hooks stand down.
        assert kwargs["env"]["BEAKR_HOOK_ACTIVE"] == "1"
        spawned.append(args)

    monkeypatch.setattr("beakr_cli.commands.memory.subprocess.Popen", fake_popen)
    stdin = json.dumps({"transcript_path": str(CLAUDE_FIXTURE)})

    with respx.mock(assert_all_called=False) as router:
        result = CliRunner().invoke(
            app, ["compound", "--hook", "--client", "claude-code", "--background"], input=stdin
        )
        assert not router.calls

    assert result.exit_code == 0
    (args,) = spawned
    assert args[1:3] == ["-m", "beakr_cli.main"]
    assert "--background" not in args
    assert "--hook" in args
    assert "--detached-child" in args
    assert args[args.index("--from-transcript") + 1] == str(CLAUDE_FIXTURE.resolve())


@respx.mock
def test_compound_by_conversation_id() -> None:
    _, compounded = _mock_import_and_compound()
    result = CliRunner().invoke(app, ["compound", "conv-9"])
    assert result.exit_code == 0
    assert _body(compounded) == {"conversation_id": "conv-9"}


def test_compound_needs_exactly_one_source() -> None:
    assert CliRunner().invoke(app, ["compound"]).exit_code == 2


@respx.mock
def test_codex_sessions_are_imported_once_per_file_version(isolated_home: Path) -> None:
    sessions = isolated_home / ".codex" / "sessions" / "2026" / "10" / "08"
    sessions.mkdir(parents=True)
    shutil.copy(FIXTURES / "codex_rollout.jsonl", sessions / "rollout-a.jsonl")
    imported, compounded = _mock_import_and_compound()

    first = CliRunner().invoke(app, ["compound", "--from-codex-sessions", "--json"])
    assert first.exit_code == 0, first.output
    assert _body(imported)["client"] == "codex"
    assert _body(imported)["session_id"] == "codex-session-1"
    assert compounded.call_count == 1
    assert str(sessions / "rollout-a.jsonl") in read_state("codex_sessions.json")

    second = CliRunner().invoke(app, ["compound", "--from-codex-sessions", "--json"])
    assert second.exit_code == 0
    assert imported.call_count == 1
    assert json.loads(second.stdout) == []


# ---------------------------------------------------------------------------
# conversations
# ---------------------------------------------------------------------------


@respx.mock
def test_conversations_ls_follows_cursor_and_filters() -> None:
    route = respx.get(f"{API}/v1/conversations").mock(
        side_effect=[
            httpx.Response(
                200, json={"data": [{"id": "c1"}], "next_cursor": "n1", "has_more": True}
            ),
            httpx.Response(200, json={"data": [{"id": "c2"}], "next_cursor": None}),
        ]
    )

    result = CliRunner().invoke(app, ["conversations", "ls", "--client", "codex", "--json"])

    assert result.exit_code == 0
    assert [c["id"] for c in json.loads(result.stdout)] == ["c1", "c2"]
    assert route.calls[0].request.url.params["client"] == "codex"
    assert route.calls[1].request.url.params["cursor"] == "n1"


@respx.mock
def test_conversations_import_from_file(tmp_path: Path) -> None:
    imported, _ = _mock_import_and_compound()
    source = tmp_path / "chat.json"
    source.write_text(
        json.dumps({"session_id": "s1", "messages": [{"role": "user", "content": "hi"}]})
    )

    result = CliRunner().invoke(
        app, ["conversations", "import", "--file", str(source), "--client", "chatgpt"]
    )

    assert result.exit_code == 0, result.output
    assert _body(imported) == {
        "client": "chatgpt",
        "session_id": "s1",
        "messages": [{"role": "user", "content": "hi"}],
        # Not interactive unless the person passes --interactive.
        "interactive": False,
    }


def test_conversations_rm_refuses_without_yes_when_not_interactive() -> None:
    assert CliRunner().invoke(app, ["conversations", "rm", "c1"]).exit_code == 2


@respx.mock
def test_conversations_show_missing_maps_to_exit_4() -> None:
    respx.get(f"{API}/v1/conversations/nope").mock(
        return_value=httpx.Response(404, json={"detail": "Not found"})
    )
    assert CliRunner().invoke(app, ["conversations", "show", "nope"]).exit_code == 4


@respx.mock
def test_conversations_import_from_transcript_is_incremental() -> None:
    imported, compounded = _mock_import_and_compound()
    args = ["conversations", "import", "--from-transcript", str(CLAUDE_FIXTURE), "--json"]

    assert CliRunner().invoke(app, args).exit_code == 0
    assert CliRunner().invoke(app, args).exit_code == 0

    # The engine appends on re-import; the second run must send nothing.
    assert imported.call_count == 1
    assert not compounded.called


# PROSE-392: a transcript import always keeps something. Beakr refuses one that
# keeps nothing (`summary_required`), so the CLI sends a summary by default.


@respx.mock
def test_a_transcript_import_sends_a_client_side_summary_by_default() -> None:
    imported, _ = _mock_import_and_compound()
    args = ["conversations", "import", "--from-transcript", str(CLAUDE_FIXTURE), "--json"]
    assert CliRunner().invoke(app, args).exit_code == 0
    body = _body(imported)
    assert "store_full_text" not in body and "hold_text" not in body
    assert body["summary"].endswith("(Client-side summary by beakr-cli.)")
    assert "First request:" in body["summary"]


@respx.mock
def test_a_given_summary_replaces_the_client_side_one() -> None:
    imported, _ = _mock_import_and_compound()
    args = [
        "conversations", "import", "--from-transcript", str(CLAUDE_FIXTURE),
        "--summary", "Calibrated the pump.", "--json",
    ]  # fmt: skip
    assert CliRunner().invoke(app, args).exit_code == 0
    assert _body(imported)["summary"] == "Calibrated the pump."


@respx.mock
def test_full_text_needs_no_summary() -> None:
    imported, _ = _mock_import_and_compound()
    args = [
        "conversations", "import", "--from-transcript", str(CLAUDE_FIXTURE),
        "--full-text", "--json",
    ]  # fmt: skip
    assert CliRunner().invoke(app, args).exit_code == 0
    body = _body(imported)
    assert body["store_full_text"] is True and "summary" not in body


@respx.mock
def test_compound_holds_the_text_and_asks_for_the_capture() -> None:
    imported, compounded = _mock_import_and_compound()
    args = [
        "conversations", "import", "--from-transcript", str(CLAUDE_FIXTURE),
        "--compound", "--json",
    ]  # fmt: skip
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    assert _body(imported)["compound"] is True
    assert compounded.called


def test_the_client_side_summary_is_deterministic_and_bounded() -> None:
    from beakr_cli.transcripts import ParsedSession, client_summary

    long = "x" * 1000
    session = ParsedSession(
        session_id="s",
        title="Pump work",
        messages=[
            {"role": "user", "content": "first ask"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": long},
        ],
    )
    summary = client_summary(session)
    assert summary == client_summary(session)
    assert summary.startswith("Session: Pump work.") and '"first ask"' in summary
    assert len(summary) < 700
    assert client_summary(ParsedSession(session_id="s", messages=[])) is None


# POST /v1/compound/cancel (engine #1662): `conversations compound cancel` stops
# a compound and says how to resume; a 200 `nothing_to_cancel` is reported as
# such, not as a cancel.
@respx.mock
def test_conversations_compound_cancel_stops_and_names_the_resume(monkeypatch) -> None:
    monkeypatch.setattr("beakr_cli.output.is_piped", lambda: False)
    route = respx.post(f"{API}/v1/compound/cancel").mock(
        side_effect=[
            httpx.Response(
                200, json={"conversation_id": "cv1", "status": "cancelled", "pending_messages": 2}
            ),
            httpx.Response(
                200,
                json={
                    "conversation_id": "cv1",
                    "status": "nothing_to_cancel",
                    "pending_messages": 0,
                },
            ),
        ]
    )

    cancelled = CliRunner().invoke(app, ["conversations", "compound", "cancel", "cv1"])
    nothing = CliRunner().invoke(app, ["conversations", "compound", "cancel", "cv1"])

    assert cancelled.exit_code == 0, cancelled.output
    assert json.loads(route.calls[0].request.content) == {"conversation_id": "cv1"}
    assert "Cancelled compounding of cv1. 2 message(s) are kept." in cancelled.stdout
    assert "beakr compound cv1" in cancelled.stdout
    assert nothing.exit_code == 0, nothing.output
    assert "Nothing to cancel" in nothing.stdout
