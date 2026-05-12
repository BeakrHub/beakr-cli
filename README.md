# beakr-cli

CLI and MCP server for [Beakr's](https://thebeakr.com) knowledge base.

## CLI vs MCP — when to use which

Beakr ships both surfaces, and `beakr setup` installs both. They wrap the same backend.

- **CLI** (`beakr research`, `beakr kb …`) — for you, in a terminal or shell script.
- **MCP server** (`beakr mcp`) — for Claude Code and Codex. Starts automatically once registered; the assistant calls it through tools like `research` and `kb_search`. You don't invoke it directly.

If you only want one, install both anyway — they don't conflict and the MCP server is a thin wrapper over the same API the CLI uses.

## One-line install

```bash
curl -fsSL https://raw.githubusercontent.com/BeakrHub/beakr-cli/main/install.sh | sh
```

(Once `install.thebeakr.com` is wired up, `curl -fsSL https://install.thebeakr.com | sh` will work too — same script, prettier URL.)

That's it. The script:

1. Installs [`uv`](https://github.com/astral-sh/uv) if missing (no system Python required — `uv` ships its own).
2. Installs the `beakr` CLI via `uv tool install beakr-cli`, putting a real `beakr` binary on PATH at `~/.local/bin/beakr`.
3. Prompts for your Beakr API key (or reads `$BEAKR_API_KEY`) and saves it.
4. Registers the Beakr MCP server in **Claude Code** (`claude mcp add` or `~/.claude.json`) and **Codex** (`~/.codex/config.toml`) — whichever it detects.
5. Copies the Beakr skill and `kb-*` slash commands into `~/.claude` and/or `~/.codex`.

Restart Claude Code or Codex once and the `beakr` MCP server appears under `/mcp`. You can also use the CLI directly: `beakr kb search "..."`, `beakr research "..."`, etc.

Pass flags through the pipe if you want non-interactive behavior:

```bash
# Skip the API key prompt (auth later with `beakr auth login`)
curl -fsSL https://raw.githubusercontent.com/BeakrHub/beakr-cli/main/install.sh | sh -s -- --no-auth

# Only wire Claude Code, force-overwrite anything that exists
curl -fsSL https://raw.githubusercontent.com/BeakrHub/beakr-cli/main/install.sh | sh -s -- --client claude --force
```

## Manual install

If you'd rather do it step-by-step:

```bash
# 1. Install the CLI globally (any of these works)
uv tool install beakr-cli       # recommended
pipx install beakr-cli
pip install beakr-cli           # only inside an isolated env

# 2. Authenticate
beakr auth login

# 3. Wire it into your assistant(s) — auth + skills + MCP in one step
beakr setup

# Or pick parts:
beakr setup --no-mcp       # skills only (the old `beakr install`)
beakr setup --no-skills    # MCP registration only
beakr setup --client codex # only Codex
beakr setup --uninstall    # remove everything
```

The MCP registration uses the absolute path of the installed `beakr` binary, so it works even when Claude Code or Codex is launched from a GUI without your shell PATH.

## Manual MCP registration (no CLI)

If you don't want the CLI at all and just want the MCP server entry:

**Claude Code** — add to `.mcp.json` in your project, or `~/.claude.json` for user scope:

```json
{
  "mcpServers": {
    "beakr": {
      "command": "uvx",
      "args": ["--from", "beakr-cli", "beakr", "mcp"],
      "env": { "BEAKR_API_KEY": "<your-key>" }
    }
  }
}
```

**Codex** — add to `~/.codex/config.toml`:

```toml
[mcp_servers.beakr]
command = "uvx"
args = ["--from", "beakr-cli", "beakr", "mcp"]
env = { BEAKR_API_KEY = "<your-key>" }
```

`uvx` fetches `beakr-cli` on demand, so no global install is needed — but you also won't have the `beakr` CLI on your PATH.

## Quick start

```bash
# Browse knowledge base
beakr kb ls
beakr kb ls --roots              # top-level pages only
beakr kb ls --parent "My Page"   # children of a page
beakr kb cat "Page Title"
beakr kb search "authentication flow"
beakr kb blame "API Design"
beakr kb sources "API Design"
beakr kb log

# Ask a question
beakr research "what's our deploy process?"

# Propose wiki writes for review
beakr kb propose create --project <project-id> --title "API Design" --file api-design.md
beakr kb propose create --mine --title "Scratch Note" --content "Personal note"
beakr kb propose edit --project <project-id> "API Design" --patches @patches.json
beakr kb propose edit --project <project-id> "Launch Plan" --file launch.md --sections @sections.json
beakr kb proposals list
beakr kb proposals list --mine
beakr kb proposals show <proposal-id>
beakr kb proposals accept <proposal-id>

# Output JSON (for piping)
beakr kb ls --json
```

`--sections` accepts a JSON array with section IDs, optional event dates, and
citations. Each section ID should match a `<!-- sec:ID -->` marker in the page
content. Event dates should be full ISO dates like `2026-04-01`; use
`date_precision` (`day`, `month`, `quarter`, `year`, or `approx`) to express
the intended precision. Put inline citation tokens like `{{source_type:source_id}}`
or `{{!source_type:source_id}}` directly in the markdown after every factual
claim, table row/value, date, title, and relationship, then use the same keys in
`sections[].citations` with `stance` (`support`, `qualifies`, or `contradicts`)
so section provenance can roll up the inline citations. The CLI and MCP tools
preflight-check that section IDs match `<!-- sec:ID -->` markers, event dates are
valid, inline tokens are well formed, and citation keys match the inline tokens.
Citations can reference existing Beakr sources from `beakr kb sources` or
`beakr kb provenance`, external identifiers, or inline agent/user-supplied
context. To cite the current session or another source that is not in the wiki
yet, use a stable key such as `agent_note:launch-risk`, set `source_type` to
`conversation`, `agent_note`, or `user_note`, and provide `source_title` plus
`meta.excerpt`, `meta.content`, or `meta.text`.

Write proposals require exactly one project scope. Use `--project <project-id>`
for shared/team knowledge or `--mine` to resolve and use your personal project.

## What gets installed

- **CLI binary**: `~/.local/bin/beakr` (from `uv tool install`)
- **Config**: `~/.beakr/config.json` (API key + default project)
- **Claude Code skill**: `~/.claude/skills/beakr/`
- **Claude Code slash commands**: `~/.claude/commands/kb-*.md` (`/kb-search`, `/kb-research`, `/kb-write`, `/kb-audit`)
- **Claude Code MCP entry**: registered via `claude mcp add` or written to `~/.claude.json`
- **Codex skill**: `~/.codex/skills/beakr/`
- **Codex MCP entry**: `[mcp_servers.beakr]` in `~/.codex/config.toml`

The skill teaches the assistant *when* and *how* to call the MCP tools (research workflows, propose-then-accept patterns, citation discipline). The MCP server provides the actual tools: `research`, `kb_ls`, `kb_cat`, `kb_search`, `kb_grep`, `kb_blame`, `kb_sources`, `kb_provenance`, `kb_links`, `kb_timeline`, `kb_log`, `kb_stats`, `kb_graph`, `list_projects`, `get_profile`, `kb_propose_create`, `kb_propose_edit`, `kb_propose_patch`, `kb_propose_find_replace`, `kb_propose_move`, `kb_propose_archive`, `kb_list_proposals`, `kb_show_proposal`, `kb_accept_proposal`, `kb_dismiss_proposal`.

MCP write tools follow the same proposal workflow as the CLI. They require
exactly one scope: `project_id` for project knowledge or `personal=true` for the
user's personal project. They do not write immediately; the assistant should show
or list the proposal and only call `kb_accept_proposal` after the user explicitly
asks to apply that proposal.

## Project-scoped install

To install skills and MCP into the *current project* (committed, shared with teammates) instead of your user-global config:

```bash
beakr setup --scope project
```

This writes:

- **Claude Code** — skills to `./.claude/skills/beakr/`, slash commands to `./.claude/commands/`, MCP entry to `./.mcp.json` (`claude mcp add --scope project`). The `.mcp.json` is designed to be committed.
- **Codex** — skills to `./.agents/skills/beakr/`. **Codex has no project-scope MCP** (its `config.toml` is global), so the MCP registration falls back to `~/.codex/config.toml` and `beakr setup` prints a warning when this happens.

## Environment variables

| Variable | Description |
|----------|-------------|
| `BEAKR_API_KEY` | API key (overrides stored config) |
| `BEAKR_API_URL` | API base URL (default: `https://api.thebeakr.com`) |
| `BEAKR_ORG_ID` | Active org UUID or slug for `X-Org-Id` |
| `BEAKR_PROJECT_ID` | Project scope |
| `CLAUDE_CONFIG_DIR` | Claude Code config directory (default: `~/.claude`). If set, `beakr setup` installs skills there and falls back to `claude mcp add` rather than editing `~/.claude.json`. |
| `CODEX_HOME` | Codex config directory (default: `~/.codex`). `beakr setup` writes skills and `config.toml` here. |

## Release

Releases publish to PyPI from GitHub Actions when a GitHub release is published.
Configure a PyPI Trusted Publisher for this project. For the first release, use
PyPI's pending publisher flow if the `beakr-cli` project does not exist yet:

- PyPI project: `beakr-cli`
- Owner: `BeakrHub`
- Repository name: `beakr-cli`
- Workflow filename: `publish.yml`
- Environment name: `pypi`

Then release:

```bash
# Bump both versions to the same value
$EDITOR pyproject.toml src/beakr_cli/__init__.py

# Verify locally
python -m pip install --upgrade build twine
python -m build
twine check dist/*

# Commit, tag, push, and publish a GitHub release for the tag.
# The publish workflow uploads dist/* to PyPI.
```

After the workflow succeeds:

```bash
uv tool install beakr-cli   # or: pipx install beakr-cli
beakr version
```

### Hosting the install script

`install.thebeakr.com` should serve `install.sh` from this repo's `main` branch with `Content-Type: text/x-shellscript` (or just `text/plain`). Easiest options:

- **Cloudflare Pages**: deploy this repo, set a custom domain `install.thebeakr.com`, and a redirect rule serving `install.sh` at `/`.
- **GitHub Pages + CNAME**: enable Pages on this repo, alias `install.thebeakr.com` to it, and copy `install.sh` to `index.html` (or use a `_redirects` file).
- **Static bucket**: upload `install.sh` to an S3/R2 bucket fronted by `install.thebeakr.com`.

Until the domain is wired, users can run the script directly from GitHub:

```bash
curl -fsSL https://raw.githubusercontent.com/BeakrHub/beakr-cli/main/install.sh | sh
```
