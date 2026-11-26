"""Keep request URLs out of the stdio MCP server's log (its stderr).

``beakr mcp`` builds an mcp ``MCPServer``, whose ``configure_logging`` puts a
RichHandler at INFO on the root logger. httpx logs every request at INFO with
its full URL ("HTTP Request: PUT <url> ..."), and an upload's presigned S3 URL
carries its signature in the query string: a credential, written to whatever
captures the MCP client's stderr. The request loggers stay at WARNING unless
``BEAKR_LOG_HTTP=1`` asks for them, and even then a signed URL's signature,
credential and token are masked.

The CLI's other commands configure no logging, so Python's last-resort
handler prints WARNING and above only and these lines never appear
(tests/test_request_logs.py checks both).
"""

from __future__ import annotations

import logging
import os
import re

#: Loggers that write a request's method and URL.
REQUEST_LOGGERS = ("httpx", "httpcore")

#: Set to 1 in the server's environment to see them (debugging).
ENV = "BEAKR_LOG_HTTP"

#: Query parameters of a signed URL that are credentials (S3, GCS, Azure SAS).
_SIGNED = re.compile(
    r"(?i)([?&](?:x-amz-(?:signature|credential|security-token)|x-goog-(?:signature|credential)"
    r"|signature|sig)=)[^&\s\"'<>]+"
)


def redact(text: str) -> str:
    """``text`` with every signed URL's credential values masked."""
    return _SIGNED.sub(r"\1<redacted>", text)


class _RedactSigned(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        masked = redact(message)
        if masked != message:
            record.msg, record.args = masked, None
        return True


def wanted() -> bool:
    return os.environ.get(ENV, "").strip().lower() in ("1", "true", "yes", "on")


def quiet_request_logs() -> None:
    """Request loggers at WARNING (DEBUG with ``BEAKR_LOG_HTTP=1``); mask signed URLs.

    Call it after the logging is configured (the server's construction): the
    mask goes on the root handlers that exist then.
    """
    level = logging.DEBUG if wanted() else logging.WARNING
    for name in REQUEST_LOGGERS:
        logging.getLogger(name).setLevel(level)
    for handler in logging.getLogger().handlers:
        if not any(isinstance(f, _RedactSigned) for f in handler.filters):
            handler.addFilter(_RedactSigned())
