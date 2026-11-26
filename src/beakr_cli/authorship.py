"""Who wrote an uploaded file and when: the API's own parsing, for the stdio MCP server.

A copy of beakr-engine ``beakr/api/dto/authorship.py`` (its parsers, no database),
so ``upload_files`` refuses a bad ``authors`` or ``authored_at`` before anything is
uploaded, as the hosted tool does (Octant R11). ``datetime.UTC`` is
``timezone.utc`` here, and a trailing ``Z`` is read as ``+00:00``: the CLI runs on
Python 3.10.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

#: The longest ``authored_by`` entry accepted: a name and an address (RFC 5321
#: caps an address at 254), with room for the brackets.
MAX_AUTHORED_BY_CHARS = 320
#: Authors one file may name: a co-written note has a few, never a mailing list.
MAX_AUTHORS = 20
#: The longest name kept.
MAX_AUTHOR_NAME_CHARS = 200
#: How far ahead of now an ``authored_at`` may be: a writer a day ahead in
#: another time zone has written today, not tomorrow.
FUTURE_SLACK = timedelta(days=1)
#: The earliest year a note can be dated: anything before is a typo.
EARLIEST_YEAR = 1900

_NAME_WITH_EMAIL = re.compile(r"^(?P<name>[^<>]*?)\s*<(?P<email>[^<>\s]+)>$")
_EMAIL = re.compile(r"^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class AuthoredBy:
    """``authored_by`` parsed: a name, an email, or both."""

    name: str | None
    email: str | None

    def text(self) -> str:
        """``Jane Doe <jane@example.com>``, ``Jane Doe`` or ``jane@example.com``."""
        if self.name and self.email:
            return f"{self.name} <{self.email}>"
        return self.name or self.email or ""

    def key(self) -> str:
        """What makes two entries one author: the address, else the folded name."""
        return (self.email or "").lower() or (self.name or "").casefold()


def _refuse_unprintable(value: str) -> None:
    for char in value:
        category = unicodedata.category(char)
        if category == "Cc" or category == "Cf":
            raise ValueError(
                "authored_by must not contain control or formatting characters "
                f"(found U+{ord(char):04X})"
            )


def parse_authored_by(value: str) -> AuthoredBy:
    """Parse ``Jane Doe``, ``Jane Doe <jane@example.com>`` or ``jane@example.com``.

    Raises ValueError for anything else: angle brackets that are not one address,
    an address that is not one, or a name over ``MAX_AUTHOR_NAME_CHARS``.
    """
    _refuse_unprintable(value)
    text = " ".join(value.split())
    if not text:
        raise ValueError("authored_by is empty")
    match = _NAME_WITH_EMAIL.match(text)
    if match:
        name = match.group("name").strip().strip('"').strip() or None
        email = match.group("email").strip().lower()
        if not _EMAIL.match(email):
            raise ValueError(f"authored_by: {email!r} is not an email address")
    elif "<" in text or ">" in text:
        raise ValueError(
            "authored_by must be a name, 'Name <email>' or an email address, "
            "e.g. 'Jane Doe <jane@example.com>'"
        )
    elif _EMAIL.match(text):
        name, email = None, text.lower()
    else:
        name, email = text, None
    if name and len(name) > MAX_AUTHOR_NAME_CHARS:
        raise ValueError(f"authored_by: the name is over {MAX_AUTHOR_NAME_CHARS} characters")
    return AuthoredBy(name=name, email=email)


def parse_authored_at(value: str, *, now: datetime | None = None) -> str:
    """``authored_at`` as stored: ``2026-06-14``, or a UTC time ``2026-06-14T09:30:00Z``.

    A date-time without an offset is read as UTC. Raises ValueError for any
    other text, a date before ``EARLIEST_YEAR``, or one more than
    ``FUTURE_SLACK`` ahead of now.
    """
    text = value.strip()
    if not text:
        raise ValueError("authored_at is empty")
    current = now or datetime.now(timezone.utc)
    if _DAY.match(text):
        try:
            day = date.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"authored_at: {text!r} is not a date") from exc
        moment = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        stored = day.isoformat()
    else:
        try:
            # Python 3.10's fromisoformat does not read a trailing Z (3.11 does).
            parsed = datetime.fromisoformat(
                text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
            )
        except ValueError as exc:
            raise ValueError(
                f"authored_at: {text!r} is not a date (YYYY-MM-DD) or an ISO 8601 date-time"
            ) from exc
        moment = (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(
            timezone.utc
        )
        stored = moment.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    if moment.year < EARLIEST_YEAR:
        raise ValueError(f"authored_at: {text!r} is before {EARLIEST_YEAR}")
    if moment > current + FUTURE_SLACK:
        raise ValueError(f"authored_at: {text!r} is in the future")
    return stored


def authored_day(stored: str | None) -> date | None:
    """The day of a stored ``authored_at`` (its UTC day for a date-time), or None."""
    if not stored:
        return None
    try:
        return date.fromisoformat(stored[:10])
    except ValueError:
        return None


def parse_authors(value: str | list[str]) -> list[AuthoredBy]:
    """``authored_by`` as authors, in order, each named once: a string is one author.

    An empty string or list is no authors (the metadata update reads it as
    "clear"). Raises ValueError for an empty entry inside a list, an entry
    ``parse_authored_by`` refuses, or more than ``MAX_AUTHORS``.
    """
    entries = [value] if isinstance(value, str) else list(value)
    if isinstance(value, str) and not value.strip():
        return []
    authors: list[AuthoredBy] = []
    seen: set[str] = set()
    for entry in entries:
        if not entry.strip():
            raise ValueError("authored_by: an author entry is empty")
        author = parse_authored_by(entry)
        if author.key() in seen:
            continue
        seen.add(author.key())
        authors.append(author)
    if len(authors) > MAX_AUTHORS:
        raise ValueError(f"authored_by names more than {MAX_AUTHORS} authors")
    return authors


def display_authors(names: list[str]) -> str | None:
    """``Jane Doe``, ``Jane Doe and Bob Smith``, ``Jane Doe, Bob Smith and Ana Li``."""
    names = [name for name in names if name]
    if not names:
        return None
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} and {names[-1]}"
