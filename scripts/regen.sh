#!/usr/bin/env bash
# Refresh the engine snapshot, regenerate what beakr-cli derives from it, then lint:
#
#   scripts/regen.sh [ENGINE_PATH]
#
# - snapshots/beakr-engine (the engine files CI checks against, pinned in
#   snapshots/manifest.json; see scripts/refresh_snapshots.py)
# - src/beakr_cli/data/operations.json   (`beakr api`, from docs/api/public-openapi.json)
# - src/beakr_cli/data/agent_tool_surface.json (from docs/api/agent-tool-surface.json)
# - src/beakr_cli/_private_api/_generated  (the allowlisted unlisted operations,
#   from docs/api/cli-private-openapi.json, the snapshot's filtered internal contract)
#
# ENGINE_PATH defaults to $BEAKR_ENGINE_PATH, then ../beakr-engine. Everything is
# generated from the refreshed snapshot, so
# what is committed is exactly what CI checks. The SDKs regenerate in their own
# repositories (BeakrHub/beakr-python, BeakrHub/beakr-typescript); take a new
# Python SDK with scripts/pin_sdk.sh.
set -euo pipefail

cd "$(dirname "$0")/.."
if [[ $# -gt 0 ]]; then
  uv run python scripts/refresh_snapshots.py refresh --engine "$1"
else
  uv run python scripts/refresh_snapshots.py refresh
fi
snapshot="snapshots/beakr-engine"
uv run python scripts/sync_operations.py "$snapshot"
uv run python scripts/sync_tool_surface.py "$snapshot"
uv run python scripts/generate_private_api.py "$snapshot"
# No --fix: it would rewrite generated files.
uv run ruff check .
