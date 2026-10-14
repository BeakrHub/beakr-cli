"""Run the engine's real conversation merge for the CLI tests, one request per line.

Started with the engine's own Python (its dependencies are not the CLI's). Reads
one JSON request per line on stdin and answers one JSON line on stdout:

    {"base_index": 2 | null, "messages": [{"role": ..., "content": ...}]}
    -> {"held": [...contents...], "rewrote": bool}
    -> {"conflict": expected}        (the engine's ImportConflictError)
    {"reset_to": n}                  -> {"held": [...]}  (simulate a server behind)

State (hashes and held messages) lives in this process for the whole test.
"""

import json
import sys

from beakr.services.conversations.importing import (
    ImportConflictError,
    IncomingMessage,
    _merge,
)

hashes: list[str] = []
held: list[dict | None] = []
for line in sys.stdin:
    request = json.loads(line)
    if "reset_to" in request:
        hashes, held = hashes[: request["reset_to"]], held[: request["reset_to"]]
        reply = {"held": [m["content"] for m in held if m]}
    else:
        incoming = [IncomingMessage(m["role"], m["content"]) for m in request["messages"]]
        try:
            hashes, held, _first, rewrote = _merge(
                hashes, held, incoming, request.get("base_index")
            )
            reply = {"held": [m["content"] for m in held if m], "rewrote": rewrote}
        except ImportConflictError as exc:
            reply = {"conflict": exc.expected}
    sys.stdout.write(json.dumps(reply) + "\n")
    sys.stdout.flush()
