# beakr-cli

CLI and MCP server for [Beakr's](https://thebeakr.com) knowledge base.

## Install

```bash
pipx install beakr-cli
```

`pipx` is recommended so the `beakr` binary is on `PATH` globally — MCP clients
spawn `beakr` from their own environment and won't see a project-local venv.
`pip install beakr-cli` works too if you already have an isolated environment.

## Quick start

```bash
# Authenticate (prompts for API key, then project selection)
beakr auth login

# Use sandbox environment
beakr auth login --env sandbox

# Browse knowledge base
beakr kb ls
beakr kb ls --roots              # top-level pages only
beakr kb ls --parent "My Page"   # children of a page
beakr kb cat "Page Title"
beakr kb search "authentication flow"
beakr kb blame "API Design"
beakr kb sources "API Design"
beakr kb log

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
the intended precision. Citations can reference existing Beakr sources from
`beakr kb sources` or `beakr kb provenance`, external identifiers, or inline
agent/user-supplied context. To cite the current session, use an inline source
with `source_type` set to `conversation`, `agent_note`, or `user_note`, plus
`source_title` and `meta.excerpt`, `meta.content`, or `meta.text`. The backend
assigns a real source id when creating the proposal.

Write proposals require exactly one scope. Use `--project <project-id>` for
shared/team knowledge or `--mine` to resolve and use your personal space.
Non-personal spaces do not have wikis.

## MCP server (Claude Code integration)

```bash
beakr mcp
```

Add to `.mcp.json` in your project root:

```json
{
  "mcpServers": {
    "beakr": {
      "command": "beakr",
      "args": ["mcp"],
      "env": {
        "BEAKR_API_URL": "https://api.thebeakr.com",
        "BEAKR_API_KEY": "<your-key>"
      }
    }
  }
}
```

This gives Claude direct access to your knowledge base via tools: `research`,
`kb_ls`, `kb_cat`, `kb_search`, `kb_grep`, `kb_blame`, `kb_sources`,
`kb_provenance`, `kb_links`, `kb_timeline`, `kb_log`, `kb_stats`, `kb_graph`,
`list_spaces`, `list_projects`, `get_profile`, `kb_propose_create`,
`kb_propose_edit`, `kb_propose_patch`, `kb_propose_find_replace`,
`kb_propose_move`, `kb_propose_archive`, `kb_list_proposals`,
`kb_show_proposal`, `kb_accept_proposal`, and `kb_dismiss_proposal`.

MCP write tools follow the same proposal workflow as the CLI. They require
exactly one scope: `project_id` for project knowledge or `personal=true` for the
user's personal space. They do not write immediately; the assistant should show
or list the proposal and only call `kb_accept_proposal` after the user explicitly
asks to apply that proposal.

## Skills (Claude Code & Codex)

The CLI ships with a Beakr skill and slash commands you can install into your
Claude Code or Codex setup. They teach the assistant *when* and *how* to use
the MCP tools (research workflows, propose-then-accept patterns, citation
discipline).

```bash
# Install for all detected clients (~/.claude and/or ~/.codex)
beakr install

# Install for one client explicitly
beakr install --client claude
beakr install --client codex

# Install into the current project (./.claude and ./.agents/skills) — committed,
# shared with teammates
beakr install --scope project

# Overwrite existing files
beakr install --force

# Remove
beakr install --uninstall
```

What gets installed:

- **Claude Code:** `~/.claude/skills/beakr/` (skill + examples + reference) and
  `~/.claude/commands/kb-*.md` (slash commands: `/kb-search`, `/kb-research`,
  `/kb-write`, `/kb-audit`).
- **Codex:** `~/.codex/skills/beakr/` (same skill, invoked as `$beakr` or via
  `/skills`).

Restart Claude Code or Codex after installing if the skill doesn't appear.

## Environment variables

| Variable | Description |
|----------|-------------|
| `BEAKR_API_KEY` | API key (overrides stored config) |
| `BEAKR_API_URL` | API base URL (default: `https://api.thebeakr.com`) |
| `BEAKR_ORG_ID` | Active org UUID or slug for `X-Org-Id` |
| `BEAKR_PROJECT_ID` | Project scope |

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
pipx install beakr-cli
beakr version
```
