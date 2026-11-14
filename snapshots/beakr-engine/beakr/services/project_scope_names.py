"""Matching a project NAME a caller typed to the projects it can read.

A Knowledge call names its project as ``scope``, and the name is typed by a
model or a person: ``"Maxs Personal"`` for "Max's Personal", a curly apostrophe
for a straight one, doubled spaces. An exact (case-insensitive) match found
nothing, and the refusal (``scope_not_found: Could not resolve project scope:
Maxs Personal``) named no project, so the caller guessed again or gave up.

Two rules, both over the projects the caller's session can read (RLS decides
which; nothing here widens it):

* **Normalized match.** Names compare after Unicode compatibility folding,
  casefolding, dropping apostrophes and quotation marks and collapsing
  whitespace. A reference whose normalized form equals exactly one readable
  project's is that project; two or more are ambiguous, as an exact duplicate
  name already is.
* **Suggestions.** When nothing matches, the closest readable names (by
  similarity of the normalized forms, then by containment) are offered.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from collections.abc import Iterable, Sequence
from uuid import UUID

#: Apostrophes and quotation marks a typed name may carry, drop or swap.
_QUOTES_RE = re.compile(r"['\"`‘’‚‛“”„´ʼ′]")
_SPACE_RE = re.compile(r"\s+")

#: Similarity a suggested name must reach (``difflib`` ratio of normalized forms).
SUGGESTION_CUTOFF = 0.6

#: Names a refusal offers at most.
MAX_SUGGESTIONS = 3


def normalized_project_name(name: str) -> str:
    """``name`` as the normalized match compares it."""
    folded = unicodedata.normalize("NFKC", name or "").casefold()
    return _SPACE_RE.sub(" ", _QUOTES_RE.sub("", folded)).strip()


def normalized_matches(ref: str, projects: Iterable[tuple[UUID, str]]) -> tuple[UUID, ...]:
    """The projects whose normalized name equals ``ref``'s, ordered by id."""
    key = normalized_project_name(ref)
    if not key:
        return ()
    return tuple(
        sorted(
            {project_id for project_id, name in projects if normalized_project_name(name) == key},
            key=str,
        )
    )


def closest_project_names(
    ref: str, names: Sequence[str], *, limit: int = MAX_SUGGESTIONS
) -> list[str]:
    """The readable project names closest to ``ref``, best first."""
    key = normalized_project_name(ref)
    if not key:
        return []
    by_key: dict[str, str] = {}
    for name in sorted(names):
        by_key.setdefault(normalized_project_name(name), name)
    close = difflib.get_close_matches(key, list(by_key), n=limit, cutoff=SUGGESTION_CUTOFF)
    containing = [candidate for candidate in by_key if candidate not in close and key in candidate]
    return [by_key[candidate] for candidate in [*close, *containing][:limit]]


def did_you_mean_projects(names: Sequence[str]) -> str:
    """The suggestion sentence a scope refusal ends with (empty without names)."""
    if not names:
        return ""
    quoted = ", ".join(f'"{name}"' for name in names)
    return f" -- did you mean {quoted}? list_projects names every project you can read."
