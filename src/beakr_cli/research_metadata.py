"""Keep research coverage and measured usage visible across response formats."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

_USAGE_FIELDS = (
    "cost_usd",
    "duration_s",
    "engine",
    "lead_model",
    "turns",
    "subtasks",
    "stopped_early",
)


def _write_usage(usage: dict[str, Any]) -> None:
    # Optional benchmark accounting. Never include questions, error messages,
    # sources or credentials; a failed request's cost is unknown, not zero.
    destination = os.environ.get("BEAKR_RESEARCH_USAGE_LOG")
    if destination:
        try:
            with Path(destination).open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"cost_usd": None, **usage}) + "\n")
        except OSError:
            logging.getLogger(__name__).warning("Could not record research usage", exc_info=True)


def record_failed_research(error: BaseException, duration_s: float) -> None:
    """Record an attempted API call without inventing usage or masking its error."""
    _write_usage(
        {
            "status": "cancelled" if isinstance(error, asyncio.CancelledError) else "error",
            "error_type": type(error).__name__,
            "client_duration_s": round(duration_s, 3),
        }
    )


def with_cited_labels(data: dict[str, Any]) -> dict[str, Any]:
    """Bridge older research prompts without rewriting source evidence or prose."""
    result = dict(data)
    for field in ("answer", "text"):
        if isinstance(result.get(field), str):
            result[field] = re.sub(
                r"\[verified:\s*([^\]\n]+)\]", r"[cited: \1]", result[field]
            ).replace("[knowledge only]", "")
    return result


def attach_research_metadata(body: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    result = dict(body)
    text = str(result.get("content") or "")
    for field, label in (("checked", "Checked"), ("not_checked", "Not checked")):
        if data.get(field):
            line = f"{label}: {data[field]}"
            if line not in text:
                text += "\n\n" + line
    result["content"] = text
    if isinstance(data.get("coverage"), dict):
        # Incomplete retrieval need not have hit a time/step budget. Keep the
        # backend's explicit coverage and continuation instead of inferring it.
        result["coverage"] = {**(result.get("coverage") or {}), **data["coverage"]}
    if data.get("stopped_early"):
        result["coverage"] = {
            **(result.get("coverage") or {}),
            "complete": False,
            "stopped_early": True,
            "not_checked": data.get("not_checked") or "Research ended at its time or step limit.",
        }
    usage = {key: data[key] for key in _USAGE_FIELDS if key in data}
    if usage:
        result["research_usage"] = usage
    _write_usage(usage)
    return result
