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
# Three cases:
#   a) uv-managed beakr-cli already installed -> upgrade
#   b) a `beakr` binary exists from another install method (pipx, pip)
#      -> install with --force so uv replaces it
#   c) nothing installed yet -> plain install
if uv tool list 2>/dev/null | grep -q '^beakr-cli '; then
  log "Upgrading beakr-cli ..."
  uv tool upgrade beakr-cli
elif command -v beakr >/dev/null 2>&1; then
  log "Existing 'beakr' binary detected from another install method; reinstalling via uv (--force) ..."
  uv tool install --force beakr-cli
else
  log "Installing beakr-cli ..."
  uv tool install beakr-cli
fi

# 3. Make sure the freshly-installed `beakr` is on PATH for the setup step.
export PATH="$HOME/.local/bin:$PATH"

if ! command -v beakr >/dev/null 2>&1; then
  err "beakr CLI installed but not on PATH. Open a new shell and run: beakr setup"
  exit 1
fi

# 4. Run the one-shot setup: auth prompt + skills + MCP registration for any
#    detected client (Claude Code, Codex). Pass through any user flags.
log "Running 'beakr setup' ..."
beakr setup "$@"

cat <<'EOF'

beakr: All set. If `beakr` isn't on PATH in a new terminal, add this to your shell profile:
  export PATH="$HOME/.local/bin:$PATH"
EOF
