"""Who wrote a page revision, as ``beakr kb log`` prints it.

A revision's ``compiled_by`` is a storage stamp (``proposal:<uuid>``,
``api:<user uuid>:beakr-cli``, ``human:<uuid>``). ``kb log <page>`` printed it
raw, while the same history filtered (``--by``, ``--via``) named the person and
their agent, because that one is the Knowledge door's text. The revisions route
carries the same facts on every row: ``writer`` (the door's own words for a
proposal or a direct API write) and ``attribution`` (``kind``, ``person``,
``agent``, ``client``, ``model``, ``process``, ``accepted_by``).
"""

from __future__ import annotations

from typing import Any

__all__ = ["revision_writer"]


def revision_writer(revision: dict[str, Any]) -> str:
    """``Dana Reyes via codex/1.4 (proposal 179c343c, accepted by auto-approve)``, or the stamp.

    The engine's sentence when it sends one, else the same reading built from
    ``attribution``; the raw stamp only when the row names nobody (an older
    engine, or a writer the reader may not see).
    """
    writer = revision.get("writer")
    if isinstance(writer, str) and writer.strip():
        return writer.strip()
    stamp = str(revision.get("compiled_by") or "")
    attribution = revision.get("attribution")
    if isinstance(attribution, dict):
        person = attribution.get("person")
        through = attribution.get("agent") or attribution.get("client")
        lead = " via ".join(str(part) for part in (person, through) if part)
        if attribution.get("kind") == "beakr":
            # Beakr's own write: its model or process, and who asked for it.
            own = attribution.get("model") or attribution.get("process") or stamp or "Beakr"
            return f"{own} (for {lead})" if lead else str(own)
        if lead:
            accepted = attribution.get("accepted_by")
            return f"{lead} (accepted by {accepted})" if accepted else lead
    return str(revision.get("editor_name") or stamp or "system")
