# beakr-cli

CLI and MCP server for [Beakr](https://thebeakr.com): your team's
Knowledge, projects, files and integrations, from a terminal, a script, or an agent.

The CLI and SDKs cover Beakr's documented API, which is deliberately small:
sign-in and keys, organization members and service accounts, projects, files,
Knowledge (read, write, proposals, research), agent runs, interviews,
conversations, integrations, and the embedded-mode pieces (external users,
labels, end-user tokens). Run `beakr --help` for the commands; `beakr api` has
one command per documented operation.

## CLI vs MCP — when to use which

Beakr ships both surfaces, and `beakr setup` installs both. They wrap the same backend.

- **CLI** (`beakr research`, `beakr kb …`) — for you, in a terminal or shell script.
- **MCP server** (`beakr mcp`) — for Claude Code and Codex. Starts automatically once registered; the assistant calls it through `research`, `knowledge_base`, and `knowledge_base_write`. You don't invoke it directly.

If you only want one, install both anyway — they don't conflict and the MCP server is a thin wrapper over the same API the CLI uses.

## One-line install

```bash
curl -fsSL https://raw.githubusercontent.com/BeakrHub/beakr-cli/main/install.sh | sh
```

(Once `install.thebeakr.com` is wired up, `curl -fsSL https://install.thebeakr.com | sh` will work too — same script, prettier URL.)

That's it. The script:

1. Installs [`uv`](https://github.com/astral-sh/uv) if missing (no system Python required — `uv` ships its own).
2. Installs the `beakr` CLI from GitHub (`uv tool install git+https://github.com/BeakrHub/beakr-cli`), putting a real `beakr` binary on PATH at `~/.local/bin/beakr`. It needs `git`.
   (PyPI's newest `beakr-cli` is 0.3.x, which is older than this CLI and talks only to production;
   install from GitHub until 0.4 is published there.)
3. Signs you in through the browser, or verifies `$BEAKR_API_KEY` and saves it to the keychain so Claude Code and Codex can use it.
4. Registers the Beakr MCP server in **Claude Code** (`claude mcp add` or `~/.claude.json`) and **Codex** (`~/.codex/config.toml`) — whichever it detects — pointing at the `beakr` that ran setup, with your API URL and `BEAKR_HOME` when they are not the defaults.
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
uv tool install git+https://github.com/BeakrHub/beakr-cli    # recommended
pipx install git+https://github.com/BeakrHub/beakr-cli
pip install git+https://github.com/BeakrHub/beakr-cli        # only inside an isolated env

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

The MCP registration uses the absolute path of the `beakr` binary that ran setup (not whichever `beakr` is first on PATH), so it works even when Claude Code or Codex is launched from a GUI without your shell PATH. A non-default API (`BEAKR_BASE_URL`, or the URL you signed in to) and `BEAKR_HOME` are written into the registration's `env`; the API key is not — it stays in the keychain, or `credentials` under `BEAKR_HOME`.

## Manual MCP registration (no CLI)

If you don't want the CLI at all and just want the MCP server entry:

**Claude Code** — add to `.mcp.json` in your project, or `~/.claude.json` for user scope:

```json
{
  "mcpServers": {
    "beakr": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/BeakrHub/beakr-cli", "beakr", "mcp"],
      "env": { "BEAKR_API_KEY": "<your-key>" }
    }
  }
}
```

**Codex** — add to `~/.codex/config.toml`:

```toml
[mcp_servers.beakr]
command = "uvx"
args = ["--from", "git+https://github.com/BeakrHub/beakr-cli", "beakr", "mcp"]
env = { BEAKR_API_KEY = "<your-key>" }
```

`uvx` fetches `beakr-cli` from GitHub on demand, so no global install is needed — but you also won't have the `beakr` CLI on your PATH. Add `"BEAKR_BASE_URL": "https://api-sandbox.thebeakr.com"` (or your self-hosted URL) to `env` to use an API other than production.

## Updating

```bash
beakr update           # upgrade to the latest release and refresh installed skills
beakr update --check   # only report whether a newer release exists
beakr version          # installed version, plus the latest release on PyPI
```

`beakr update` upgrades with the tool that installed it (`uv tool` or `pipx`) and then
re-copies the skill and slash commands wherever they are already installed, so they
match the new MCP tools. Restart Claude Code / Codex afterwards to load the new server.

You don't have to remember to check:

- Interactive CLI commands print a one-line notice when a newer release exists
  (checked against PyPI at most once a day, cached in `~/.beakr/update_check.json`).
- The MCP server tells the assistant when it is out of date, and exposes
  `beakr_version` and `update_beakr` tools so you can ask it to update Beakr.

Installs that cannot be upgraded safely in place are reported, not changed: a
`uv tool` install from a local checkout, `pip` inside another environment, or `uvx`
(which reuses a cached copy; use `uvx --refresh --from git+https://github.com/BeakrHub/beakr-cli beakr mcp`
to always run the newest version). Set `BEAKR_NO_UPDATE_CHECK=1` to turn the check off.

## Shell completion

Tab completion covers every command, subcommand and option (including the
generated `beakr api` commands) in bash, zsh, fish and PowerShell.

```bash
beakr --install-completion   # detect your shell, install completion, then restart the shell
beakr --show-completion      # print the script for your current shell instead
```

`--install-completion` writes to:

| Shell | Script | Loaded by |
|-------|--------|-----------|
| bash | `~/.bash_completions/beakr.sh` | a `source` line appended to `~/.bashrc` |
| zsh | `~/.zfunc/_beakr` | `fpath+=~/.zfunc; autoload -Uz compinit; compinit` appended to `~/.zshrc` |
| fish | `~/.config/fish/completions/beakr.fish` | fish, automatically |
| PowerShell | your PowerShell profile | the profile |

To generate the script for a specific shell (dotfiles, a Dockerfile, a shell other
than the one you are in), set `_BEAKR_COMPLETE` to `source_<shell>`:

```bash
_BEAKR_COMPLETE=source_zsh  beakr > ~/.zfunc/_beakr
_BEAKR_COMPLETE=source_bash beakr > ~/.bash_completions/beakr.sh   # bash 4.4+
_BEAKR_COMPLETE=source_fish beakr > ~/.config/fish/completions/beakr.fish
```

The scripts call `beakr` to compute matches, so `beakr` must be on your `PATH`
(a `uvx`-only setup has no completion). Deprecated command aliases are not offered.

## Quick start

Install, sign in, bind a project, upload, ask, read a card:

```bash
uv tool install git+https://github.com/BeakrHub/beakr-cli   # or the one-line installer above
beakr auth login                   # browser sign-in; works with SSO and MFA
beakr init --create "Research"     # bind this directory to a new project
beakr upload docs/ notes.pdf       # folders keep their structure
beakr research "what did we decide about the launch date?"
beakr kb sections "launch date"    # find the card
beakr kb cat "Launch Plan"
```

`beakr init` writes `.beakr/project.json`; inside that directory every command
that takes `--project` uses it by default. `--project` (also `-P`, and `--scope`,
the word the MCP tools use) takes a project id, slug or exact name. Project
resolution order: the flag, `BEAKR_PROJECT_ID`, the nearest
`.beakr/project.json`, then the default set by `beakr auth set-scope`.
When the MCP server starts inside a bound directory or with `BEAKR_PROJECT_ID`
set, the tools that read that project by default (`documents`, `research`,
`wiki_stats`, `wiki_graph`) are listed without their `project` parameter, so
an agent works in the pinned project instead of guessing a name.

At a terminal, results are tables and text. When stdout is not a terminal (an
agent or a script), each object is one tab-separated line -- its id, its name,
then a few `key=value` fields -- followed by a `next:` line naming the command
to run next when there is one (`beakr jobs wait ...` after an upload). Every
command takes `--json` for the full objects; `BEAKR_OUTPUT=json|compact|human`
forces one format. No command prompts when stdin is not a terminal. Exit codes
are stable:

| Code | Meaning |
|------|---------|
| 0 | ok |
| 1 | error |
| 2 | usage (bad flags, or a confirmation needed without `--yes`) |
| 3 | not signed in, or the credential lacks a scope |
| 4 | not found |
| 5 | conflict |
| 6 | a person must do it: the project requires a person to accept proposals, commit Knowledge or approve an agent's upload (`person_review_required`) |

## Signing in and API keys

```bash
beakr auth login                    # opens your browser to sign in
beakr auth login --no-browser       # print the sign-in link instead of opening it
beakr auth login --device           # a one-time code to approve from another device
echo "$KEY" | beakr auth login --with-api-key   # scripts and CI (or a hidden prompt at a terminal)
beakr auth status                   # which credential is active and when it expires
beakr auth token                    # print the current access token (refreshed if needed)
beakr whoami
beakr auth logout                   # revokes the sign-in at its issuer, then forgets it
```

Signing in stores a short-lived access token and a refresh token
in the OS keychain (falling back to `~/.beakr/credentials`, mode 0600). The CLI,
the MCP server and the SDK refresh it automatically, one process at a time under
a lock, because refreshing rotates the pair. `beakr auth token --json` prints the
access token (never the refresh token) for the current API, or the one named by
`--api-url`, refreshing it first when it is about to expire; the TypeScript SDK
uses it to reach a keychain sign-in and to refresh one without taking that lock
itself.

The API names the sign-in issuer, the CLI's OAuth client and the Beakr scopes
to ask for (`GET /v1/auth/config`; Clerk for Beakr's hosted API). `beakr auth
login` runs the OAuth authorization-code flow with PKCE in your browser: it
listens once on `127.0.0.1` on a free port, opens the issuer's sign-in page, and
takes the code from the redirect to `http://127.0.0.1:<port>/callback`, refusing
a redirect whose `state` is not its own. `--no-browser` prints the link for you
to open on the same machine. `--device` uses the issuer's device grant (a
one-time code you confirm from any browser); when the issuer does not offer it,
the command says so and points to `--with-api-key`. The token carries only the Beakr
scopes it was granted; if `beakr doctor` reports a missing scope, sign in again.
`beakr auth logout` revokes the refresh token at the issuer, then removes the
local sign-in. Sign-ins saved by CLI versions before this one used Beakr's own
device flow, which the API no longer serves: sign in again.

API keys can be limited to scopes and projects and expire (90 days by default).
They are created in the web app, from a signed-in browser session: the API
refuses key creation from an API key or a CLI sign-in token, so a leaked token
cannot mint itself a long-lived key. `beakr keys create` opens that page; with
`--scope`, `--project`, `--expires-in` or `--name` the form opens filled in, and
you review it and press Create there.

```bash
beakr keys create                   # opens <app>/settings?tab=api-keys
beakr keys create --name CI --scope kb:read --project Platform --expires-in 30d
beakr keys scopes                   # what each scope allows
beakr keys ls
beakr keys revoke <key-id>
```

Scopes used by the documented API: `kb:read`, `kb:write`, `projects:read`,
`projects:admin`, `members:admin`, `uploads:write`, `integrations:read`,
`integrations:admin`, `conversations:read`, `conversations:write`, `agent:run`,
`interviews:read` and `interviews:write` (`beakr keys scopes` lists every scope a
key can carry). A call outside a key's scopes exits 3 and names the missing scope.

## Org, projects and files

```bash
beakr projects ls | create NAME | show ID | archive ID | rm ID
beakr projects status -P ID | retry -P ID [--file FILE_ID]   # Knowledge: ready? what failed?
beakr projects profile show -P ID | set -P ID ... | edit -P ID # what Knowledge should understand
beakr projects members ls ID | add ID USER_ID | invite ID EMAIL | rm ID TYPE PRINCIPAL_ID
beakr members ls | role USER_ID admin | rm USER_ID
beakr upload report.pdf data/ --project <id> --folder <folder-id>
beakr files upload a.pdf b.pdf data/ --project <id>       # the same command, beside files ls
beakr files ls --search budget | show ID | download ID --out ./ | rm ID
beakr integrations ls | connect PROVIDER | status ID | rm ID
beakr integrations tools --provider slack           # what your connected integrations can answer
beakr integrations call slack_search --arg query="assay handover"
beakr doctor                                        # auth, scopes, API, MCP, hooks, version
```

### Tell Knowledge what a project is about

A project's Knowledge profile steers how its files are compiled: what the
project is for, the people, organizations and topics that matter, and the kinds
of things to build pages for. Set it before or after uploading; Knowledge reads
it when it next compiles files. Only the project's owners may change it.

```bash
beakr projects profile set -P Basel \
  --description "Batch records, deviations and CAPAs for the Basel site" \
  -t batch -t deviation -t capa -t supplier \
  --person "Priya Natarajan:Batch owner" --goal "Track deviations to closure"
beakr projects profile set -P Basel --file profile.yaml   # the whole profile (JSON or YAML)
beakr projects profile edit -P Basel                      # as YAML in $EDITOR
beakr projects profile show -P Basel
```

Flags change only the fields they name (a repeated flag replaces that list) and
create the profile when the project has none; `--file` replaces the whole
profile and refuses unknown fields.

### Know when Knowledge is ready

Uploaded files reach a project's Knowledge in the background after indexing.
Before asking questions that depend on new files, wait for it, see what failed,
and retry:

```bash
beakr upload docs/ -P Research --wait-knowledge     # index, then wait for Knowledge
#   Knowledge: 12 queued, 3 running, 0 failed
#   Knowledge: 0 queued, 0 running, 1 failed
#   Failed: docs/scan.pdf  processing_failed  The file could not be read, ...
#   exit 1 when an uploaded file failed to index or to reach Knowledge
beakr projects status -P Research                   # ready / queued / compiling, and failures
#   Knowledge: compiling (3 running, 12 queued)
#   Files: 120 files, 103 processed, 2 not sent or out of date
#   Failed (1):
#     docs/scan.pdf  processing_failed  The file could not be read, ...  (FILE_ID)
beakr projects retry -P Research                    # every failed file, or --file FILE_ID
beakr projects refusals Research --window 7d        # model refusals and fallbacks, by file
```

`--wait-knowledge` waits until nothing in the project is queued or running
(`--timeout` seconds), so it also waits out other files compiling at the same
time; it reports failures only for the files it uploaded, and each uploaded
file's `knowledge_status` as `beakr files ls` prints it (`created` once it is
in Knowledge). A file whose compile saved only part of its changes reads
`incomplete` and exits 1, like a failure; `beakr projects retry` resumes it. A
retry is safe to repeat: files already queued or running are left alone. The MCP server's
`list_projects` names projects whose Knowledge is still building or has failed
files, so an agent knows when an answer may be incomplete.

Organization admins manage service accounts (non-human members that own scoped
keys, for CI and backends). Creating one and minting its keys need a browser
session (Organization settings > Developer > Service accounts); listing,
disabling and `show` work from the CLI:

```bash
beakr org show
beakr org service-accounts ls | create "CI bot" --role member | disable ID --yes
beakr org service-accounts key ID --scope kb:read --project <id>   # key shown once
```

### Building Beakr into your product

A backend signed in as a service account mints short-lived tokens for its own
users, limited to projects (or to an external user's memberships) and scopes:

```bash
beakr end-user-tokens mint --subject customer-42 --project <id> --scope kb:read --ttl 900
beakr end-user-tokens ls --subject customer-42
beakr end-user-tokens revoke TOKEN_ID
```

### Interviews

Send someone a link; Beakr runs the interview and you read the result or file
it into projects:

```bash
beakr interviews link create "Assay handover" --goal "How the binding assay is run" -q "Controls?"
beakr interviews link show LINK_ID
beakr interviews link send LINK_ID --project <id>
beakr interviews link ls | revoke LINK_ID
```

## Knowledge cards

Project-map cards hold Facts, Connections and dated Findings. Knowledge reads
four ways, and they combine: as a file system (`kb ls`, `kb cat`, `kb grep`), a
graph (links, hover, timeline), a database (`kb rows`) or by meaning (`kb
sections`). `kb rows` is every card's rows as one table; each row shows its
entity (card title), entity_type (page type), the field, the value (or to),
When and sources, and the header names the table. `--match exact|similar|meaning`
sets how `--attribute` finds a field. Without `--match`, a field with exactly
that name wins; when none has it, the default match also takes names holding
its words and the closest name by meaning (where the rows index is on).
`--view conflicts` lists one card stating different values for a field at the
same When; `--view fields` lists the field names. Writes stage a proposal
(or apply at once when your auto-approve is on):

```bash
beakr kb rows --table facts --entity "Stripe" --current-only
beakr kb rows --table facts --type matter --attribute "start date" --match similar
beakr kb rows --table facts --view conflicts
# where enabled: one SELECT over every card's rows, parsed (never run as SQL)
beakr kb query "SELECT value, COUNT(DISTINCT entity) AS cards FROM facts AS OF CURRENT_DATE WHERE entity_type = 'matter' AND field = 'Current status' GROUP BY value ORDER BY cards DESC"
beakr kb add-fact "Stripe" --attribute "contract renewal" --value "2027-01-31" \
    --evidence "{{doc:abc123}}" --rationale "From the signed order form"
beakr kb add-connection "Stripe" --relation "billing provider for" --to "Platform" \
    --evidence "{{doc:abc123}}" --rationale "..."
beakr kb add-finding "Stripe" --date 2026-10-01 --kind decision \
    --text "Stay on Stripe through 2027" --evidence "{{doc:abc123}}" --rationale "..."
# cite what was said in this conversation: {{conversation:LABEL}} with the words cited
beakr kb add-fact "Stripe" --attribute "account owner" --value "Ana Ruiz" \
    --cite-conversation call-with-ana --excerpt "Ana runs the Stripe account." \
    --source-title "Call with Ana" --rationale "Said on the call"
beakr kb edit-rows "Stripe" --table facts \
    --old-row '{"attribute": "tier", "value": "Basic"}' --row '{"attribute": "tier", "value": "Pro"}' \
    --rationale "Upgraded"
beakr kb proposals ls
beakr kb proposals show <proposal-id>   # prints its content hash
beakr kb proposals accept <proposal-id> --reviewed-hash <content-hash>
```

## Agents: context, session capture, skills

```bash
beakr hooks install                 # Claude Code: ~/.claude/settings.json
beakr hooks install --client codex  # Codex: a Beakr block in ~/.codex/AGENTS.md
beakr hooks status
beakr hooks uninstall
beakr hooks capture on              # this repository: allow automatic capture (off by default)
beakr hooks learning on             # this repository: let your typed sessions teach personal learning (off by default)
beakr codex                         # run Codex with fresh context; capture if allowed
```

Capture needs two opt-ins (spec step 8.1 item 3): installing the hooks is your
choice as a user, and `capture: true` in a repository's `.beakr/project.json`
is the project's. Set it with `beakr hooks capture on` (or `beakr init
--capture`) in a bound repository; `beakr hooks capture off` withdraws it and
drops that repository's queued retries. In a repository without it, the hooks
never upload or queue anything, and the session-start context says capture is
off. A deliberate `beakr compound --from-transcript` works either way.

Personal learning from captured sessions is a third, separate opt-in:
`learn_from_sessions: true` in `.beakr/project.json`, set by `beakr hooks
learning on` (or `beakr init --learning`), off by default and reset when the
directory is bound to another project. Each import tells Beakr whether a
person typed the session (`interactive`), and it says `true` only when the
project has this opt-in **and** the agent's own transcript classifies the
session as human: Claude Code `entrypoint: cli` with a typed prompt, or a
Codex session whose `source` is `cli` or `vscode`. Headless runs (`claude -p`
and the Agent SDK, `entrypoint: sdk-*`; `codex exec`; MCP-driven and subagent
sessions) and sessions without these markers are sent as `false`. The value
is decided when the session is captured and recorded on each queued retry; a
retry never sends it as more interactive, and withdrawing the opt-in sends
pending retries as `false`. `beakr conversations import` sends `false` unless
you pass `--interactive`, which is refused for a transcript recorded as
headless.
`beakr setup --uninstall` removes the hooks and the Codex block along with the
skills and MCP server.

For Claude Code this adds three hooks, merged into your existing settings
without touching other hooks (re-running is safe):

- **SessionStart** runs `beakr context --format claude_md --project auto --hook`
  and injects your org, project and personal context into the session, with a
  line saying whether session capture is on for this project.
- **Stop** runs `beakr compound --hook --client claude-code --background`. Stop
  fires after every turn, so capture is incremental: a per-session cursor in
  `~/.beakr/hooks_state.json` records what was sent, and only messages added
  since are imported (Beakr appends them to the same conversation). Queueing
  the conversation for compounding is throttled to once per 15 minutes per
  session.
- **SessionEnd** does the same with `--force`, so the last turns are always
  sent and compounded.

Imported conversations are private to you. Hooks never block or fail the
session: problems go to `~/.beakr/hooks.log`, and an import that fails (offline,
API down) is kept in `~/.beakr/hook_queue/` (at most 50 entries) and sent first
on the next run; `beakr doctor` reports the queue. Every process a hook starts
carries `BEAKR_HOOK_ACTIVE=1`, and a hook that sees it does nothing, so capture
cannot recurse.
Codex has no hooks, so `beakr codex` is its pre-session wrapper: it rewrites
the Beakr block in `~/.codex/AGENTS.md` with the bound project's current
context, runs `codex` with every argument passed through (and its exit code),
then imports the sessions written during the run if the repository allows
capture. The block ends with a line naming the repository and saying whether
capture is on for it, as Claude's SessionStart line does; it is computed from
local consent, so `beakr hooks capture on|off` and `beakr init` rewrite it at
once, and a refresh that cannot reach Beakr still rewrites it (and drops context
left from another project). A failed refresh or import is reported, never a
failed Codex run.
`beakr compound --from-codex-sessions` still imports finished sessions by hand. `beakr setup --hooks` installs hooks along with the MCP server and
skills (interactive setup asks; non-interactive setup skips them unless
`--hooks` is passed).

```bash
beakr context --question "billing migration" --format markdown
beakr compound --from-transcript ~/.claude/projects/.../session.jsonl --client claude-code
beakr conversations ls --client claude-code
beakr conversations search "pump calibration" --any   # your sessions and chats that answered it
```

The MCP server adds `context`, `skills`, `conversations_search` and
`import_conversation` beside `research`, `knowledge_base` and
`knowledge_base_write`. Card rows and writes are `knowledge_base` command
`rows` and `knowledge_base_write` actions `add_fact`, `add_connection`,
`add_finding` and `edit_rows`.

The MCP server lists every tool: Claude Code calls only the tools a server
lists. For a smaller listing, set `BEAKR_MCP_TOOLS=eager` in the server's
environment (for example `"env": {"BEAKR_MCP_TOOLS": "eager"}` in the MCP
entry): it lists the eager set (`context`, `list_projects`, `research`,
`knowledge_base`, `knowledge_base_write`, `read_file`, `skills`, `workboard`)
plus `discover_tools`, which returns every other tool's name, description and
input schema and adds the tools it finds to the listing (the server sends
`notifications/tools/list_changed`, so the client reads the listing again).

Every Knowledge write the MCP server makes records its agent (`X-Beakr-Agent`):
the MCP client's own name and version from its `initialize` handshake
(`claude-code/2.1.283`, `codex-mcp-client/0.40.0`). When several agents run
under one key, give each its own label with `BEAKR_AGENT` in its MCP entry
(`"env": {"BEAKR_AGENT": "reviewer"}`); it overrides the client's name.

On a self-hosted Beakr installation, which leaves out hosted features
(organization administration, external users, end-user tokens, service
accounts, integrations, skills), the server does not list the tools for what
the installation lacks: it reads the installation's own
`/v1/public/openapi.json`, and a tool whose call meets a route the server does
not have answers "This Beakr installation does not provide ..." and leaves the
listing. CLI commands for those areas print the same sentence, and
`beakr doctor` lists them.

Beakr's hosted MCP server (`https://<api>/mcp`) exposes the same tools, including
`beakr_version` and `update_beakr`. Those two answer for the server that runs
them: here they report and upgrade this installed CLI; the hosted server reports
its own version and the latest beakr-cli release it is configured with, and its
`update_beakr` runs nothing and returns the command to run locally (`beakr update`).

When a `documents grep` file listing exceeds MCP's text budget, both servers
retain complete rows and report a continuation offset after the last visible
row. Continue with the same project, query and filters at that offset before
advancing an `after_document` scan cursor. Scan limits and unindexed-document
warnings remain visible; a page of results does not imply an exhaustive scan.
Unrecognized response formats keep the generic truncation warning and narrower
request guidance. This formatting does not change the SDK or terminal CLI's
API response, perform additional searches, or summarize source evidence.

### Skills

A skill is a SKILL.md folder your agent loads on demand. `beakr skills ls`
lists every skill you can use or install, with its visibility, owner (`me`,
`org`, `system` for built-ins, `anyone` for public skills from outside your
organization), origin (`created`, `mined`, `imported`, `system`) and whether it
is installed in your library.

```bash
beakr skills ls --mine                 # yours; --org, --public, --builtin combine
beakr skills ls --available --public   # public skills you could install
beakr skills ls --origin mined         # the ones Beakr drafted and you kept
beakr skills show plate-reader         # its SKILL.md (--json: labels, revision, files)
beakr skills download plate-reader ./  # ./plate-reader/ (--zip for a zip)
beakr skills add ./plate-reader        # a SKILL.md folder or a .zip of one
beakr skills install lab-notebook-entry
beakr skills uninstall lab-notebook-entry
beakr skills rm plate-reader --yes     # delete a skill you wrote
beakr skills proposals                 # drafted from your work: approve or dismiss
beakr skills sync                      # your library <-> ~/.claude/skills, ~/.codex/skills
```

A skill is named by its id or its slug; a slug means your own skill first, then
a built-in, then one you installed, then your organization's.

## Python SDK

The CLI is built on Beakr's Python SDK, `beakr-sdk` (`import beakr_sdk`), which
lives in its own repository, [BeakrHub/beakr-python](https://github.com/BeakrHub/beakr-python),
with its documentation, generator and tests. Installing the CLI installs it, so
scripts in the same environment can use it:

```python
from beakr_sdk import Beakr

beakr = Beakr()  # api_key=..., else BEAKR_API_KEY, else your `beakr auth login`
print(beakr.knowledge.sections("launch date", scope="Research"))
```

The TypeScript SDK, `@beakr/sdk`, is
[BeakrHub/beakr-typescript](https://github.com/BeakrHub/beakr-typescript).

The SDK covers only the documented contract. The CLI and its MCP server also
call a few routes the API serves without documenting them (profile,
memories, outputs, workflows, org administration...), through the CLI's own
private client, `beakr_cli._private_api`. Each of those operations is listed in
`src/beakr_cli/_private_api/operations.allowlist`, and only those are generated
(`scripts/generate_private_api.py` renders them with the SDK's generator from
the engine's internal contract, filtered to those operations; this repository
keeps only that filtered part, in `snapshots/beakr-engine/docs/api/cli-private-openapi.json`);
`tests/test_private_api_allowlist.py` fails when a call site and the list
disagree.

### The Python SDK dependency

Until `beakr-sdk` is on PyPI, `pyproject.toml` requires `beakr-sdk>=0.1.0,<0.2`
and `[tool.uv.sources]` resolves it from git, pinned to a pushed commit of
BeakrHub/beakr-python. To take a newer SDK:

```bash
scripts/pin_sdk.sh <commit-or-tag>   # or no argument: beakr-python's current main
uv sync --extra dev
```

To install both from local checkouts (say, to try an SDK change with the CLI),
tell uv to ignore `[tool.uv.sources]`; otherwise the git pin and the local path
are two URLs for `beakr-sdk` and the install fails with "conflicting URLs":

```bash
uv pip install --no-sources ./beakr-python ./beakr-cli
```

To develop the CLI against a local SDK checkout, point the source at it while
you work (do not commit this):
`beakr-sdk = { path = "../beakr-python", editable = true }`.

Once `beakr-sdk` is published to PyPI:

1. delete the `beakr-sdk = { git = ... }` line from `[tool.uv.sources]`;
2. keep (or narrow) the `beakr-sdk` range in `[project.dependencies]` so it
   admits the published version, and run `uv lock`.

Built wheels never carry `[tool.uv.sources]`: a `beakr-cli` wheel always
declares `beakr-sdk` as a PyPI requirement. So `publish.yml` refuses to publish
the CLI until a `beakr-sdk` version in that range is on PyPI.

## Benchmarks

`benchmarks/` holds the public compounding, corrections, false-completeness and
cross-agent handoff suites. They run only through the public API, against a
sandbox (`bk_test_` key); see `benchmarks/README.md`.

```bash
uv run python benchmarks/run.py --dry-run        # offline, exercises the harness only
```

## Knowledge base commands

```bash
# Browse knowledge base
beakr kb ls
beakr kb ls --roots              # top-level pages only
beakr kb ls --parent "My Page"   # children of a page
beakr kb cat "Page Title"
beakr kb search "authentication flow"
beakr documents search "who covers losses from defects"   # the original files, by meaning
beakr kb sources "API Design"
beakr kb log

# Ask a question
beakr research "what's our deploy process?"

# Propose wiki writes for review
beakr kb propose create --project <project-id> --title "API Design" --sections @sections.json
beakr kb propose create --mine --title "Scratch Note" --content "Personal note"
beakr kb propose edit --project <project-id> "API Design" --patches @patches.json
beakr kb propose edit --project <project-id> "Launch Plan" --file launch.md --sections @sections.json
beakr kb proposals ls
beakr kb proposals ls --mine
beakr kb proposals show <proposal-id>
# Bound to the read you reviewed: a proposal changed since is refused. At a
# terminal without --yes, accept shows the proposal and asks instead.
beakr kb proposals accept <proposal-id> --reviewed-hash <content-hash>

# Output JSON (for piping)
beakr kb ls --json
```

For `kb propose create`, `--sections` is the page: a JSON array of
`{title, body, citations?, event_start?, event_end?, date_precision?}` objects.
Beakr writes the `<!-- sec:ID -->` markers and section ids, so do not author
them. `--content` alone stages one markdown text as given; with `--sections`
entries that have no body, `--content` is the body of the one section.
Event dates should be full ISO dates like `2026-04-01`; use
`date_precision` (`day`, `month`, `quarter`, `year`, or `approx`) to express
the intended precision. Put inline citation tokens like `{{source_type:source_id}}`
or `{{!source_type:source_id}}` directly in a body after every factual
claim, table row/value, date, title, and relationship, then use the same keys in
that section's `citations` with `stance` (`support`, `qualifies`, or
`contradicts`; `supports`, `qualify` and `contradict` are read as those).
Beakr checks the citations and reports a refusal once.

For `kb propose edit` with `--content`, `--sections` annotates the content's
existing sections: each entry's `id` matches a `<!-- sec:ID -->` marker in it,
and the CLI preflight-checks that ids match markers, event dates are valid,
inline tokens are well formed, and citation keys match the inline tokens.
Citations can reference existing Beakr sources from `beakr kb sources`,
external identifiers, or inline agent/user-supplied
context. To cite the current session or another source that is not in the wiki
yet, use a stable key such as `agent_note:launch-risk`, set `source_type` to
`conversation`, `agent_note`, or `user_note`, and provide `source_title` plus
`meta.excerpt`, `meta.content`, or `meta.text`.

Where an organization or a project requires a person to accept what API keys
write, `beakr kb agent-review show [-P <project>]` says what is in force and
where it comes from; a person (or a key they created with `proposals:accept`)
changes it with `beakr kb agent-review set --proposals on` (`-P <project>` for
one project, `--follow-org` to drop a project's own policy). `set` exits 3
for a key without the `members:admin` scope, 4 where agent review is not
available on the installation (`show` says so), and 6 for a key that is not a
person's. Work that waits for a person exits 6; `beakr files approve <file-id>`
sends a held upload on.

Write proposals require exactly one project scope. Use `--project <project-id>`
for shared/team knowledge or `--mine` to resolve and use your personal project.

## What gets installed

- **CLI binary**: `~/.local/bin/beakr` (from `uv tool install`)
- **Config**: `~/.beakr/config.json` (API URL, saved API key, default project)
- **Sign-in tokens**: the OS keychain, or `~/.beakr/credentials` (0600)
- **Claude Code skill**: `~/.claude/skills/beakr/`
- **Claude Code slash commands**: `~/.claude/commands/kb-*.md` (`/kb-search`, `/kb-research`, `/kb-write`, `/kb-audit`)
- **Claude Code MCP entry**: registered via `claude mcp add` or written to `~/.claude.json`
- **Codex skill**: `~/.codex/skills/beakr/`
- **Codex MCP entry**: `[mcp_servers.beakr]` in `~/.codex/config.toml`

The skill teaches the assistant *when* and *how* to call the MCP tools (research workflows, propose-then-accept patterns, citation discipline). The MCP server exposes the engine's canonical `knowledge_base` and proposal-only `knowledge_base_write` tools. Reads return both the canonical text and structured public source descriptors: opaque `source_ref`, citation key, filename, provider, path/URL, retained excerpt and locator, and source status. External clients can reuse `source_ref` in a later write; Beakr resolves its internal Unit/version/evidence identity server-side.

The MCP contract intentionally does not expose Unit IDs, connector IDs, artifact IDs, table names, or `external_items` implementation details. Claude Code and Codex only need to know what the source is, where it lives, what exact evidence was retained, and which opaque reference to pass back.

MCP writes follow the same proposal workflow as the CLI. Pass `scope` with a
project name or ID. They do not write immediately; the assistant should show or
list the proposal and only call `accept_proposal` after the user explicitly asks
to apply that proposal, passing the `content_hash` that `show_proposal` printed as
`reviewed_hash`. A proposal that changed after it was shown is refused, and one
that changes card table rows is not accepted without the hash.

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
| `BEAKR_BASE_URL` / `BEAKR_API_URL` | API base URL (default: `https://api.thebeakr.com`) |
| `BEAKR_AGENT` | Sent as `X-Beakr-Agent` to attribute writes to an agent. The MCP server defaults to its client's name and version (`claude-code/2.1.283`); set this to tell apart several agents that share one key. The CLI at a terminal sends none. |
| `BEAKR_RUN` | Sent as `X-Beakr-Run`: groups a script's Knowledge writes into one run that `beakr kb undo-run <run>` takes back together. The MCP server defaults to one run per server process (`stdio:<id>`). |
| `BEAKR_SESSION` | Sent as `X-Beakr-Session`: the agent session behind the writes, shown on their provenance ("session <id>"). Unset, the CLI and MCP server use the host agent's own session id when it gives one: `CLAUDE_CODE_SESSION_ID` (Claude Code, in its MCP servers and commands), `CODEX_THREAD_ID` (Codex) and `CURSOR_CONVERSATION_ID` (Cursor), the last two only in the shell commands their agents run. Nothing is sent when none is set. |
| `BEAKR_APP_URL` | Web app URL for pages the CLI opens (default: saved at `beakr auth login`, else `https://app.thebeakr.com`) |
| `BEAKR_HOME` | Directory for config, credentials and hook state (default: `~/.beakr`) |
| `BEAKR_NO_KEYRING` | Set to `1` to store sign-in tokens in `~/.beakr/credentials` instead of the keychain |
| `BEAKR_ORG_ID` | Active org UUID or slug for `X-Org-Id` |
| `BEAKR_PROJECT_ID` | Project scope (overrides `.beakr/project.json`) |
| `BEAKR_NO_UPDATE_CHECK` | Set to `1` to disable the PyPI check for newer releases. |
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

### Versions and release notes come from the API contract

The `beakr api` command table and the agent tool surface are derived from the
engine, so the engine decides what a release contains:

- **Freshness.** CI never reads the engine. The engine files this repository
  is derived from and tested against are committed under
  `snapshots/beakr-engine` (laid out as in the engine), and
  `snapshots/manifest.json` pins the engine commit they came from (with
  `uncommitted_changes` when they were taken from a working tree) and each
  file's SHA-256. `.github/workflows/ci.yml` checks, on every pull request and
  push, that the snapshot is exactly what the manifest lists and that the
  `beakr api` command table, the vendored agent tool surface and the private
  client match it, then runs the tests against it. The engine's full internal
  contract is never committed: only the allowlisted operations
  (`cli-private-openapi.json`); and where a test needs one fact from an engine
  source file, `derived/` holds that fact rather than the file. One command, with an engine checkout beside
  this one (or its path as the argument), refreshes the snapshot and
  regenerates everything from it after an engine change: `scripts/regen.sh`
  (`scripts/refresh_snapshots.py diff` says whether the snapshot is stale; the
  SDKs regenerate in their own repositories; take a new one with
  `scripts/pin_sdk.sh`).
- **What the engine's CI covers.** The engine's CI checks this repository out
  on the branch named like its own (else `main`) and runs the same freshness
  checks and tests against the engine itself, so an engine change that the
  snapshot here does not have yet shows up there. Tests that run the engine's
  own code in its `.venv` (`tests/test_hook_engine_reconciliation.py`,
  `tests/test_codex_compound_text_policy_review.py`; `real_engine` in
  `tests/_engine.py`) skip here and run only there.
- **Version.** `scripts/prepare_release.py` (identical in every Beakr client
  repository; `release/config.json` names this package's version files:
  `pyproject.toml`, `beakr_cli.__version__`, `uv.lock`) compares the contract at
  the last release (the engine commit in `release/released.json`) with the
  current one, using the engine's compatibility rules (vendored in
  `scripts/contract_compat.py`). A breaking change needs a major bump (below
  1.0, a minor one), an added or changed operation a minor bump, a wording
  change a patch.
- **Notes.** The API changelog entries added since that release become the
  release's section in `CHANGELOG.md`, grouped as the API changelog groups them;
  the `### CLI and SDK` subsection is hand-written and kept across reapplies.

```bash
uv run python scripts/prepare_release.py plan    # what changed, which bump it needs
uv run python scripts/prepare_release.py apply   # bump versions, write CHANGELOG.md
uv run python scripts/prepare_release.py check   # what CI and publishing run
```

### Releasing

```bash
uv run python scripts/prepare_release.py apply --date 2026-10-10
uv run python scripts/prepare_release.py check

# Verify locally
uv build
uvx twine check dist/*

# Commit, tag v<version>, push, and publish a GitHub release for the tag.
# publish.yml runs ci.yml first (tests, freshness, release check), refuses a
# tag that does not match pyproject.toml or a beakr-sdk requirement PyPI cannot
# satisfy, then uploads dist/* to PyPI.

# Afterwards, record what was released (the engine commit it was built from),
# then refresh the snapshot so it carries that commit's contract and changelog
# (snapshots/beakr-engine/release-base/<sha>), which the offline release check reads:
uv run python scripts/prepare_release.py record --engine-commit <engine sha>
uv run python scripts/refresh_snapshots.py refresh --engine <engine checkout>
```

After the workflow succeeds:

```bash
beakr update                # or a fresh: uv tool install beakr-cli
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
