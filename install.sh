#!/bin/sh
# install.sh - Bootstrap the Beakr CLI and wire it into Claude Code / Codex.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/BeakrHub/beakr-cli/main/install.sh | sh
#
# (Future: curl -fsSL https://install.thebeakr.com | sh — same script, prettier URL.)
#
# Pass flags through to `beakr setup`:
#   curl -fsSL https://raw.githubusercontent.com/BeakrHub/beakr-cli/main/install.sh | sh -s -- --no-auth
#   curl -fsSL https://raw.githubusercontent.com/BeakrHub/beakr-cli/main/install.sh | sh -s -- --client claude --force

set -eu

log() { printf 'beakr: %s\n' "$*"; }
err() { printf 'beakr: %s\n' "$*" >&2; }

# 1. Ensure `uv` is installed (Astral's uv ships its own Python — no system Python required).
if ! command -v uv >/dev/null 2>&1; then
  log "uv not found; installing from astral.sh ..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # Make uv visible to this script even though the user's shell hasn't reloaded.
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
  if ! command -v uv >/dev/null 2>&1; then
    err "uv installed but not on PATH for this script. Open a new shell and re-run."
    exit 1
  fi
else
  log "uv detected ($(uv --version))"
fi

# 2. Install (or upgrade) the beakr CLI as a uv tool. This puts a real `beakr`
#    binary on PATH at ~/.local/bin/beakr — no `uvx` indirection at runtime.
#
#    It installs from GitHub: the newest release on PyPI (0.3.x) is older than
#    this CLI and talks only to production, so `uv tool install beakr-cli` would
#    install the wrong version. Set BEAKR_CLI_SOURCE to install from elsewhere
#    (a checkout, a branch: git+https://github.com/BeakrHub/beakr-cli@<ref>).
#    --force replaces a PyPI, pipx or pip install of the same `beakr`;
#    --refresh fetches the newest commit instead of a cached one.
BEAKR_CLI_SOURCE="${BEAKR_CLI_SOURCE:-git+https://github.com/BeakrHub/beakr-cli}"
case "$BEAKR_CLI_SOURCE" in
  git+*)
    # uv clones git sources (this CLI and its Python SDK) with the git CLI.
    if ! command -v git >/dev/null 2>&1; then
      err "git is required to install the Beakr CLI from GitHub. Install git, then re-run."
      exit 1
    fi
    ;;
esac
log "Installing beakr-cli from $BEAKR_CLI_SOURCE ..."
uv tool install --force --refresh "$BEAKR_CLI_SOURCE"

# 3. Make sure the freshly-installed `beakr` is on PATH for the setup step.
export PATH="$HOME/.local/bin:$PATH"

if ! command -v beakr >/dev/null 2>&1; then
  err "beakr CLI installed but not on PATH. Open a new shell and run: beakr setup"
  exit 1
fi

# 4. Run the one-shot setup: auth prompt + skills + MCP registration for any
#    detected client (Claude Code, Codex). Pass through any user flags.
#
# When invoked via `curl | sh`, stdin is the pipe from curl — typer prompts
# would silently skip. Redirect stdin from /dev/tty so interactive prompts
# (e.g. "Use existing API key? [Y/n]") reach the user's terminal. Fall back
# to inherited stdin when /dev/tty isn't available (CI, no controlling tty).
log "Running 'beakr setup' ..."
if [ -e /dev/tty ]; then
  beakr setup "$@" < /dev/tty
else
  beakr setup "$@"
fi

cat <<'EOF'

beakr: All set. If `beakr` isn't on PATH in a new terminal, add this to your shell profile:
  export PATH="$HOME/.local/bin:$PATH"
EOF
