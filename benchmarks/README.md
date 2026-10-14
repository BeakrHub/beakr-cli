# beakr-bench

Public benchmarks for knowledge that compounds across agents. Every suite runs
only through Beakr's public API (the `beakr_sdk` Python SDK), so a result says
what a customer's own agent would get, and anyone can rerun it against their
own sandbox.

| Suite | What it measures | Passes when |
|---|---|---|
| `compounding` | Whether answers get better as material arrives over episodes. The same questions are asked after each episode. | Before the material exists the answer abstains (no premature value); after it lands the answer uses it; later questions combine material from several episodes. The headline number is the pass rate's change from the first episode to the last. |
| `corrections` | Whether a corrected value replaces the stale one. A page is written, then corrected. | The answer gives the corrected value, cites a source, and does not repeat the stale value (which no longer appears anywhere in the project). |
| `false_completeness` | Whether an enumeration over a deliberately partial record admits it is partial. | The answer lists what is recorded, says the record may be incomplete, never claims to be complete, and never invents the members that exist in the scenario but were not written down. |
| `cross_agent_handoff` | Whether a correction made in an outside agent's session (Claude Code, Codex) reaches later answers. The session is imported and compounded. | Asked through research, and through the context route another agent loads, the answer uses the corrected value and cites where it came from. |
| `agent_onboarding` | Whether an outside agent (API agent, Claude Code, Codex) does a team task better and with fewer Knowledge lookups when it starts with Beakr's injected context. Run separately: `benchmarks/run_onboarding.py`. | Higher task success and fewer Knowledge lookups with context than without (see below). |

All case data is synthetic: the organizations, people, compounds, sites and
values are invented. The `compounding` cases follow the design of Beakr's
internal compounding evaluation (an episode clock, abstention before evidence,
questions that combine episodes) but are new material; the other three suites
are new.

## Run it

You need a sandbox organization and its test key (`bk_test_...`). Create a
sandbox while signed in to Beakr (`beakr sandbox create "bench"`, or Settings,
API keys, Sandboxes), switch to it and create a key there, then:

```bash
export BEAKR_API_KEY=bk_test_...
# from a beakr-cli checkout
uv run python benchmarks/run.py --suite all --out beakr-bench-results
# or a single suite or case
uv run python benchmarks/run.py --suite corrections --case binding-constant
```

`BEAKR_BASE_URL` points it at another deployment (for example a local engine).
A run refuses a live key (`bk_live_...`) unless you pass `--allow-live`: every
case creates a project, seeds it, and deletes it at the end (`--keep-projects`
keeps them for inspection).

Each case gets a fresh project. Seeded pages go through the Knowledge write door
like any other write; the runner accepts the proposals they stage, since a
sandbox has no reviewer. Imported conversations go through
`POST /v1/conversations/import` and `POST /v1/compound`, the path the CLI's
agent hooks use, and the runner waits for compounding to settle (up to
`--compound-timeout` seconds) before asking anything.

## Results

A run prints a Markdown summary and writes `beakr-bench-<run>.json` (every
answer, every check, every source count) and `beakr-bench-<run>.md` to `--out`.
A case that errors (the sandbox was unreachable, compounding did not finish) is
listed separately and never counted as a failed answer; the exit code is 2 when
any case errored.

## Scoring

Scoring is deterministic and lives in `beakr_bench/scoring.py`:

- `must_include`: groups of accepted spellings; one from each group must appear.
  Markdown emphasis and code marks are ignored (`**30**` matches `30`).
- `must_not_include`: stale or invented values that must not appear anywhere.
  Literal: an occurrence fails whatever surrounds it, with or without
  formatting.
- `must_not_assert` (opt-in, chosen per case): claims the answer must not make,
  for questions whose right answer is to decline. A claim fails when asserted;
  one governed by uncertainty in its own clause ("I can't confirm whether
  KB-2207 was released") passes. Uncertainty in another clause ("I can't
  confirm the date, but KB-2207 was released") does not excuse it.
- `code_must_include` / `code_must_not_include`: for tasks whose deliverable is
  code, the same as the first two, applied to the delivered code only (fenced
  blocks, or the whole answer when it has none). A stale value quoted in prose
  as a warning is not the code the agent wrote.
- `require_citation`: the response carries a source (a source list, or an
  inline `{{...}}`, `[[...]]` or `[n]` marker).
- `forbid_completeness_claim`: no "this is the complete list", "these are all",
  "no other" style claim.
- `require_partial_acknowledgement`: the answer says the record may be
  incomplete, or that it only covers what was found.

A case file records any change to how its asks are scored in that case's
`notes`. The scorer's `SCORER_VERSION` changes with any check's meaning.

### Rescoring a saved report

```bash
cd benchmarks && uv run python -m beakr_bench.rescore results/beakr-bench-<run>.json
```

scores a report's stored answers again under the current checks, with no model
calls. It writes `<name>.rescored.json` beside the original and never changes
the original. The copy's `rescore` block names the original (file, SHA-256, run
id, `generated_at`), the scorer (version and the SHA-256 of `scoring.py`), the
case files used (SHA-256 each), when it was rescored, and every ask or arm
whose result changed, before and after. A rescored report is the same run
under revised rules, not a new run.

An LLM judge can be attached for reading results (`--judge module:callable`,
returning `beakr_bench.judge.JudgeVerdict`). It is off by default, its verdict
is recorded beside the deterministic result and never replaces it, and a judge
that fails records `passed: null`, not a failure.

## Offline dry run

```bash
uv run python benchmarks/run.py --dry-run
```

runs every suite against an in-memory stand-in for the public API, through the
real SDK and its HTTP transport. It exercises the harness (loading, seeding,
compounding, polling, scoring, reporting) and is what the tests use
(`tests/benchmarks/`). It says nothing about Beakr: the stand-in is honest by
construction.

## Agent onboarding (with vs without injected context)

The developer platform's evaluation table sets this bar for agent onboarding:
tasks run with and without `context` injection in Claude Code and Codex, and
the agent should show **higher task success and fewer Knowledge lookups with
context**. `beakr_bench/onboarding/` is that eval.

Each case in `beakr_bench/data/agent_onboarding.json` seeds a fresh sandbox
project with the team knowledge a task depends on (a lab's wash buffer, a
deploy freeze, who owns an assay, a naming convention, a corrected threshold,
where a reagent lives), then gives an agent the task twice:

- **with context**: the agent starts with what Beakr's session hook would
  inject (`POST /v1/context` for the project, no question, in that hook's
  format), fetched once before either arm runs;
- **without context**: the agent starts cold.

Both arms have the same Beakr tools, so the cold agent can still look things
up. Every Beakr tool call is a Knowledge lookup. The final answer is scored
with the deterministic checks above.

| `--agent` | What runs | How context arrives |
|---|---|---|
| `api` (default) | A tool-use loop on the Anthropic Messages API (`claude-opus-5`, adaptive thinking, `--effort high`), with `research` and `knowledge_base` tools backed by the public SDK | Appended to the system prompt |
| `claude-code` | `claude -p --output-format stream-json` with the CLI's MCP server (`beakr mcp`) as its only MCP server | `--append-system-prompt`, where the SessionStart hook's `additionalContext` lands (`claude_md` format) |
| `codex` | `codex exec --json` in a scratch directory with `beakr mcp` configured | The directory's `AGENTS.md`, written exactly as `beakr hooks install` writes it (`agents_md` format) |
| `scripted` | A fixed stand-in model behind the real `api` loop (what `--dry-run` uses) | System prompt |

The report gives, per arm: task success rate, mean Knowledge lookups, turns,
input and output tokens, wall time and cost, then the bar (both comparisons
are strict; when both arms already pass every task the report says success is
at its ceiling rather than calling it a failure). Results are written as
`beakr-bench-agent_onboarding-<run>.json` and `.md`.

### Run it

```bash
# offline, free: scripted model, fake engine, real SDK and real loop
uv run python benchmarks/run_onboarding.py --dry-run

# live (sandbox key as above; the api agent also needs Anthropic credentials)
uv sync --extra bench
export BEAKR_API_KEY=bk_test_...
uv run python benchmarks/run_onboarding.py --agent api --repeats 3 --out beakr-bench-results
uv run python benchmarks/run_onboarding.py --agent claude-code --repeats 3
uv run python benchmarks/run_onboarding.py --agent codex --repeats 3
```

`--model`, `--effort`, `--max-turns` (default 8), `--case` and
`--no-fallbacks` adjust a run. The `api` agent declares the documented refusal
fallback for Claude Opus 5 and records which model served each turn, so a
fallback is visible in the report.

### Cost of a live run

Estimates for the six cases, two arms each (twelve agent runs per repeat); the
report records the measured figures.

- `api` on `claude-opus-5` ($5 / $25 per million input / output tokens): about
  $0.05-0.10 per with-context run (1-2 turns) and $0.10-0.20 per cold run
  (2-4 turns, research results re-sent each turn), so roughly **$1-2 per
  repeat, $3-6 for `--repeats 3`**. The worst case, every run hitting the
  8-turn cap, is about $0.80 per run (about $10 per repeat).
- `claude-code`: Claude Code's own system prompt adds 15-25k mostly cached
  input tokens per turn; expect about $0.10-0.30 per run, **$1-4 per repeat**,
  billed to the Claude account the CLI is signed in with. The report uses the
  `total_cost_usd` Claude Code reports.
- `codex`: billed to the Codex account or plan; the report records its tokens.
- Beakr side: every `research` lookup runs Beakr's own research in the sandbox
  org (usually 6-12 per repeat), which counts toward that org's usage.

The `claude-code` and `codex` drivers read each client's documented JSON event
stream. Their parsers are pinned by recorded streams in
`tests/benchmarks/fixtures/`, but no live run has confirmed them against the
installed client versions yet; check the first live report's turn and token
columns before relying on them.

### Evidence and interrupted runs

A live run writes, under `--evidence-dir` (default `<out>/evidence-<run>`), as
each arm run finishes:

- `runs/<case>/<arm>-r<n>.json`: the prompt, the context that arm was given (or
  none), the answer, every tool call, and the agent's transcript (for `api`,
  each model turn and each tool result; for `claude-code` and `codex`, the
  client's raw JSON event stream);
- `cases/<case>.json`: the case's seeding notes and the injected context;
- `checkpoint.jsonl`: each finished run's scored result, flushed to disk as it
  ends.

If a run is interrupted, nothing finished is lost:

```bash
uv run python benchmarks/run_onboarding.py --from-checkpoint <evidence>/checkpoint.jsonl --out results/
```

builds the report from the checkpoint without running anything.

### What the dry run proves

Nothing about any model or about Beakr. The scripted model answers from
injected context when it has some and otherwise calls `research` once, and it
answers by repeating what it was given, so it fails tasks that need a derived
answer (the batch name) or that its echo contaminates (an owner list that also
names someone else). The dry run exists so the harness (seeding, the single
context fetch, the tool loop, lookup counting, scoring and the bar) is tested
in CI for free.

## Live cross-agent handoff

The evaluation table's cross-agent bar: a correction captured by one agent is
used and cited by another. `beakr_bench/live_handoff.py` runs it with real
clients. For each `cross_agent_handoff` case it seeds the stale page, has
**Claude Code** record the correction in Beakr with its MCP tools (it stages
proposals), accepts those proposals, then asks **Codex** the suite's question
with only its Beakr MCP tools and scores the answer with the suite's checks.

```bash
export BEAKR_API_KEY=bk_test_...   # a sandbox org's key
export BEAKR_API_URL=https://...   # that sandbox's API
uv run python benchmarks/run_live_handoff.py --out results/
uv run python benchmarks/run_live_handoff.py --case codex-correction --codex-model gpt-5.5
```

- **Acceptance is the harness's, not a person's.** A sandbox has no reviewer,
  so the harness accepts every pending proposal with the run's key. The report
  and `proposals.json` say so per proposal: `accepted_by`, the key's prefix,
  the reason, the time and the result.
- **Evidence** (`--evidence-dir`, default `<out>/handoff-evidence-<run>`),
  written before each project is deleted: per case `producer.json` and
  `consumer.json` (prompt, answer, tool calls, raw event stream),
  `proposals.json` (each proposal's full body and its acceptance record),
  `accepted_content.json` (the touched pages read back after acceptance), and
  `checkpoint.jsonl` (one line per finished stage).
- **Explicit target.** The run refuses to start without `BEAKR_API_KEY` and
  `BEAKR_API_URL`. Both agents' Beakr MCP servers are given exactly those, so
  neither can fall back to a person's stored login. A `bk_live_` key needs
  `--allow-live`.
- **Cost.** About $0.30 per case for the Claude Code producer, billed to its
  signed-in account (the 2026-10-10 run was $0.96 for three cases), plus the
  Codex consumer on its own account or plan, plus the sandbox org's research
  usage.

## Adding a case

Add an entry to `beakr_bench/data/<suite>.json`. Steps are `seed_page`,
`edit_page`, `import_conversation` and `ask`; `beakr_bench/cases.py` documents
their fields and validates every file on load. Keep cases synthetic, keep every
check deterministic, and make sure an honest answer can pass: an `ask` must
never need knowledge that was not seeded.
