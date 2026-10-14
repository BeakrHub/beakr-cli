#!/usr/bin/env bash
# Pin the Python SDK (BeakrHub/beakr-python) to a pushed commit or tag, and
# relock:
#
#   scripts/pin_sdk.sh <commit-or-tag>     e.g. scripts/pin_sdk.sh v0.1.0
#   scripts/pin_sdk.sh                     the current main of BeakrHub/beakr-python
#
# Until beakr-sdk is on PyPI, pyproject.toml resolves it from git through
# [tool.uv.sources]; see the README's "The Python SDK dependency".
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
repo="https://github.com/BeakrHub/beakr-python"
ref="${1:-main}"

# A full commit id never moves; a tag or branch is resolved to the commit it
# names now, so the pin stays reproducible.
if [[ "$ref" =~ ^[0-9a-f]{40}$ ]]; then
  commit="$ref"
else
  commit="$(git ls-remote "$repo" "refs/tags/$ref^{}" "refs/tags/$ref" "refs/heads/$ref" | head -n 1 | cut -f 1)"
  if [[ -z "$commit" ]]; then
    echo "no tag or branch $ref in $repo" >&2
    exit 1
  fi
fi

python3 - "$root/pyproject.toml" "$commit" <<'PY'
import re
import sys
from pathlib import Path

path, commit = Path(sys.argv[1]), sys.argv[2]
text = path.read_text()
pattern = r'(beakr-sdk = \{ git = "https://github\.com/BeakrHub/beakr-python", rev = ")[0-9a-f]{40}(" \})'
updated, count = re.subn(pattern, rf"\g<1>{commit}\g<2>", text)
if count != 1:
    sys.exit("pyproject.toml has no beakr-sdk git pin to update")
path.write_text(updated)
PY

cd "$root"
uv lock --upgrade-package beakr-sdk
echo "beakr-sdk pinned to $repo@$commit; run uv sync (with your extras) to install it"
