"""The reference agent: a tool-use loop on the Anthropic Messages API, and its offline stand-in.

``ApiAgent`` gives a model two Beakr tools, backed by the public SDK exactly as
the CLI's MCP server backs its own (``research`` and ``knowledge_base``), and
loops until the model answers. Every Beakr tool call is a Knowledge lookup.
With context, the context Beakr would inject at session start goes into the
system prompt; without it, the system prompt carries only the task framing.

The model client is pluggable: anything with ``messages.create(**kwargs)``
returning a Messages-API-shaped response. ``anthropic_model()`` builds the real
one (``anthropic`` is an optional dependency: ``uv sync --extra bench``);
``ScriptedModel`` is the deterministic stand-in the dry run and CI use.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Protocol

from beakr_bench.extract import answer_text

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_EFFORT = "high"
DEFAULT_MAX_TURNS = 8

#: USD per million tokens (input, output), from Anthropic's published API prices.
#: Cache reads bill at a tenth of the input price. Used only to estimate cost in
#: the report; a model missing here reports no cost estimate.
PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

#: The fallback the reference agent declares for Claude Opus 5 refusals (the
#: documented array form). A fallback changes which model answered, so every
#: response's ``model`` is recorded and the report counts fallback turns.
FALLBACK_BETA = "server-side-fallback-2026-06-01"
FALLBACK_MODELS = {"claude-opus-5": "claude-opus-4-8"}

SYSTEM_PREAMBLE = (
    "You are a coding and lab-operations agent working for a team that keeps its "
    "knowledge in Beakr. Complete the user's task. When the task depends on the "
    "team's own conventions, people, values or decisions, use what you know about "
    "the team; if you do not know it, look it up with the Beakr tools before "
    "answering. Do not invent team-specific facts."
)
CONTEXT_HEADER = "## What Beakr knows about this team (injected at session start)"


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]
    ok: bool = True


@dataclass
class AgentRun:
    """One agent attempt at one task in one arm."""

    answer: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    wall_s: float = 0.0
    cost_usd: float | None = None
    models_served: list[str] = field(default_factory=list)
    stop_reason: str | None = None
    error: str | None = None
    #: What happened, kept for review: the API agent's model turns and every
    #: tool call with its result; for Claude Code and Codex, their raw JSON
    #: event streams. Written to the run's evidence file, not the report.
    transcript: list[dict[str, Any]] = field(default_factory=list)

    @property
    def knowledge_lookups(self) -> int:
        return len(self.tool_calls)


class Agent(Protocol):
    name: str
    context_format: str

    def run(self, *, prompt: str, context: str | None, project_id: str) -> AgentRun: ...


# -- Beakr tools for the API agent ----------------------------------------------

TOOLS: list[dict[str, Any]] = [
    {
        "name": "research",
        "description": (
            "Ask Beakr a question about the team's knowledge (conventions, people, "
            "decisions, values). Returns a cited answer."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
            "additionalProperties": False,
        },
    },
    {
        "name": "knowledge_base",
        "description": (
            "Run a Beakr Knowledge read command (sections, grep, cat, rows, ...) with "
            "its arguments, as the Beakr MCP knowledge_base tool does."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "arguments": {"type": "object"},
            },
            "required": ["command"],
            "additionalProperties": False,
        },
    },
]


class BeakrTools:
    """The API agent's Beakr tools, over the public SDK, scoped to one project."""

    def __init__(self, client: Any, project_id: str) -> None:
        self.client = client
        self.project_id = project_id

    def call(self, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        try:
            if name == "research":
                response = self.client.knowledge.research(
                    str(arguments.get("question") or ""), project_id=self.project_id
                )
            elif name == "knowledge_base":
                response = self.client.knowledge.relay_command(
                    str(arguments.get("command") or ""),
                    arguments.get("arguments") or {},
                    scope=self.project_id,
                    check=False,
                )
            else:
                return f"Unknown tool {name!r}.", False
        except Exception as exc:  # a tool failure is the model's to handle, not the run's
            return f"{type(exc).__name__}: {exc}", False
        return answer_text(response), True


# -- the loop ------------------------------------------------------------------


def _block(value: Any, key: str, default: Any = None) -> Any:
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def _plain_block(block: Any) -> dict[str, Any]:
    """A content block as plain data for the transcript (thinking kept as a marker only)."""
    kind = _block(block, "type")
    if kind == "text":
        return {"type": "text", "text": str(_block(block, "text", ""))}
    if kind == "tool_use":
        return {
            "type": "tool_use",
            "name": str(_block(block, "name")),
            "input": dict(_block(block, "input", {}) or {}),
        }
    # Thinking is replayed to the model unchanged but not needed for review.
    return {"type": str(kind)}


def estimate_cost(model: str, run: AgentRun) -> float | None:
    prices = PRICES_PER_MTOK.get(model)
    if prices is None:
        return None
    input_price, output_price = prices
    return round(
        (run.input_tokens * input_price + run.cache_read_tokens * input_price * 0.1) / 1_000_000
        + run.output_tokens * output_price / 1_000_000,
        6,
    )


def system_prompt(context: str | None) -> str:
    if not context:
        return SYSTEM_PREAMBLE
    return f"{SYSTEM_PREAMBLE}\n\n{CONTEXT_HEADER}\n\n{context.strip()}"


class ApiAgent:
    """A manual tool-use loop on the Messages API with the two Beakr tools."""

    name = "api"
    context_format = "markdown"

    def __init__(
        self,
        client: Any,
        model_client: Any,
        *,
        model: str = DEFAULT_MODEL,
        effort: str = DEFAULT_EFFORT,
        max_turns: int = DEFAULT_MAX_TURNS,
        fallbacks: bool = True,
        clock: Any = time.monotonic,
    ) -> None:
        self.client = client
        self.model_client = model_client
        self.model = model
        self.effort = effort
        self.max_turns = max_turns
        self.fallbacks = fallbacks and model in FALLBACK_MODELS
        self.clock = clock

    def _create(self, system: str, messages: list[dict[str, Any]]) -> Any:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 16000,
            "system": system,
            "tools": TOOLS,
            "messages": messages,
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": self.effort},
        }
        if self.fallbacks:
            return self.model_client.beta.messages.create(
                betas=[FALLBACK_BETA],
                fallbacks=[{"model": FALLBACK_MODELS[self.model]}],
                **kwargs,
            )
        return self.model_client.messages.create(**kwargs)

    def run(self, *, prompt: str, context: str | None, project_id: str) -> AgentRun:
        tools = BeakrTools(self.client, project_id)
        system = system_prompt(context)
        messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        run = AgentRun()
        started = self.clock()
        try:
            for _ in range(self.max_turns):
                response = self._create(system, messages)
                run.turns += 1
                usage = _block(response, "usage")
                run.input_tokens += int(_block(usage, "input_tokens", 0) or 0)
                run.output_tokens += int(_block(usage, "output_tokens", 0) or 0)
                run.cache_read_tokens += int(_block(usage, "cache_read_input_tokens", 0) or 0)
                served = _block(response, "model")
                if served:
                    run.models_served.append(str(served))
                content = list(_block(response, "content", []) or [])
                run.stop_reason = _block(response, "stop_reason")
                run.transcript.append(
                    {
                        "type": "model_turn",
                        "model": str(served) if served else None,
                        "stop_reason": run.stop_reason,
                        "content": [_plain_block(b) for b in content],
                    }
                )
                if run.stop_reason == "refusal":
                    run.error = "refusal"
                    break
                if run.stop_reason != "tool_use":
                    run.answer = "\n".join(
                        str(_block(b, "text", "")) for b in content if _block(b, "type") == "text"
                    ).strip()
                    if run.stop_reason == "max_tokens":
                        run.error = "max_tokens"
                    break
                # Keep every block (thinking included) so the next turn replays it unchanged.
                messages.append({"role": "assistant", "content": content})
                results = []
                for block in content:
                    if _block(block, "type") != "tool_use":
                        continue
                    arguments = dict(_block(block, "input", {}) or {})
                    text, ok = tools.call(str(_block(block, "name")), arguments)
                    run.tool_calls.append(ToolCall(str(_block(block, "name")), arguments, ok))
                    run.transcript.append(
                        {
                            "type": "tool_result",
                            "name": str(_block(block, "name")),
                            "arguments": arguments,
                            "ok": ok,
                            "result": text[:20000],
                        }
                    )
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": _block(block, "id"),
                            "content": text[:20000],
                            "is_error": not ok,
                        }
                    )
                # All tool results go back in one user message.
                messages.append({"role": "user", "content": results})
            else:
                run.error = f"no answer within {self.max_turns} turns"
        except Exception as exc:
            run.error = f"{type(exc).__name__}: {exc}"
        run.wall_s = round(self.clock() - started, 3)
        run.cost_usd = estimate_cost(self.model, run)
        return run


def anthropic_model() -> Any:
    """The real model client (``anthropic.Anthropic()``; credentials from the environment)."""
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - depends on the optional extra
        raise SystemExit(
            "The api agent needs the anthropic SDK: run `uv sync --extra bench` in beakr-cli."
        ) from exc
    return anthropic.Anthropic()


# -- the offline stand-in ------------------------------------------------------


class ScriptedModel:
    """A deterministic stand-in for the Messages API, behind the real ``ApiAgent`` loop.

    Its policy is fixed and simple, so the dry run exercises the harness (seeding,
    context fetch, tool loop, scoring, reporting) without a model:

    - if the system prompt carries injected context with any team knowledge in
      it, answer from that context and look nothing up;
    - otherwise call ``research`` once with the task, then answer from the result.

    It answers by repeating the knowledge it was given, so it passes a case only
    when that knowledge states the answer verbatim. It says nothing about any
    model or about Beakr.
    """

    model = "scripted"

    def __init__(self) -> None:
        self.messages = SimpleNamespace(create=self._create)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))
        self.requests: list[dict[str, Any]] = []

    @staticmethod
    def _tokens(text: str) -> int:
        return max(1, len(text) // 4)

    def _response(self, content: list[dict[str, Any]], stop: str, prompt_chars: int) -> Any:
        output = json.dumps(content)
        return SimpleNamespace(
            model=self.model,
            content=content,
            stop_reason=stop,
            usage=SimpleNamespace(
                input_tokens=self._tokens("x" * prompt_chars),
                output_tokens=self._tokens(output),
                cache_read_input_tokens=0,
            ),
        )

    def _create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        system = str(kwargs.get("system") or "")
        messages = kwargs["messages"]
        prompt_chars = len(system) + len(json.dumps(messages, default=str))
        knowledge = system.split(CONTEXT_HEADER, 1)[1].strip() if CONTEXT_HEADER in system else ""
        last = messages[-1]["content"]
        tool_results = [
            item
            for item in (last if isinstance(last, list) else [])
            if isinstance(item, dict) and item.get("type") == "tool_result"
        ]
        if tool_results:
            knowledge = "\n".join(str(item.get("content") or "") for item in tool_results)
        elif not _has_knowledge(knowledge):
            question = str(messages[0]["content"])
            return self._response(
                [
                    {
                        "type": "tool_use",
                        "id": f"toolu_{len(self.requests)}",
                        "name": "research",
                        "input": {"question": question},
                    }
                ],
                "tool_use",
                prompt_chars,
            )
        answer = f"From the team's records: {knowledge}"
        return self._response([{"type": "text", "text": answer}], "end_turn", prompt_chars)


def _has_knowledge(text: str) -> bool:
    lowered = text.lower()
    return bool(text.strip()) and "no records" not in lowered
