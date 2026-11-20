"""What ``beakr sources attach`` reads from a link and a provider name.

A person scripting project setup has links, not provider ids: a Notion page they
copied, a Drive folder's address bar, a Slack channel's "Copy link". This module
turns one into the item the attach call takes, and turns the provider names
people type (``notion``, ``drive``) into the provider an attachment carries
(``notion:nango``, ``gdrive:nango``).

- Notion: the 32-hex id ending the URL path (``Page-Title-<id>``, a bare id, or the
  dashed 8-4-4-4-12 form), or the ``p=`` id of a page open in peek view, which wins.
  A ``v=`` (a database view) means the item is a database. The id is sent dashed, as the
  Notion API (and so the web app's picker) spells it.
- Google Drive: ``/folders/<id>`` is a folder; ``/file/d/<id>`` and
  ``/<document|spreadsheets|presentation|...>/d/<id>`` and ``/d/<id>`` are files;
  ``?id=<id>`` says only the id, so the type must be passed.
- Slack: ``/archives/<channel id>`` (a channel's or a message's link) and the web
  client's ``/client/<team>/<channel id>``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlparse

#: Names people type for a provider, and the provider an attachment carries. The
#: integration names are the engine's (``developer_integrations
#: .NANGO_PROVIDER_BY_INTEGRATION``, what ``beakr integrations connect`` takes);
#: the rest are the short names people use. Anything else is sent as typed, so a
#: provider this table does not know (``granola``, ``s3``) still works.
PROVIDER_ALIASES: dict[str, str] = {
    "google-drive": "gdrive:nango",
    "gdrive": "gdrive:nango",
    "drive": "gdrive:nango",
    "dropbox": "dropbox:nango",
    "notion": "notion:nango",
    "google-mail": "gmail:nango",
    "gmail": "gmail:nango",
    "one-drive": "one-drive:nango",
    "onedrive": "one-drive:nango",
    "google-calendar": "gcalendar:nango",
    "gcalendar": "gcalendar:nango",
    "slack": "slack:nango",
    "outlook": "outlook:nango",
    "sharepoint-online": "sharepoint:nango",
    "sharepoint": "sharepoint:nango",
    "github": "github:nango",
    "confluence": "confluence:nango",
    "jira": "jira:nango",
    "linear": "linear:nango",
    "box": "box:nango",
    "microsoft-teams": "microsoft-teams:nango",
    "teams": "microsoft-teams:nango",
    "netsuite-tba": "netsuite:nango",
    "netsuite": "netsuite:nango",
}


def provider_slug(value: str) -> str:
    """The attachment provider for a typed name: ``drive`` -> ``gdrive:nango``."""
    typed = value.strip()
    return PROVIDER_ALIASES.get(typed.lower().replace("_", "-"), typed)


@dataclass(frozen=True)
class SourceLink:
    """The item a link names: provider, id and, when the link says, type and name."""

    provider: str
    item_id: str
    item_type: str | None = None
    name: str | None = None


class SourceLinkError(ValueError):
    """The link is not one ``attach`` can read an item from."""


_HEX32 = re.compile(r"([0-9a-fA-F]{32})$")
_DASHED = re.compile(
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$"
)
_DRIVE_ID = r"([A-Za-z0-9_-]{10,})"
#: Google editors' paths: ``/document/d/<id>/edit`` and the like.
_DRIVE_FILE_PATHS = (
    re.compile(rf"/file/d/{_DRIVE_ID}"),
    re.compile(
        rf"/(?:document|spreadsheets|presentation|forms|drawings|uc)(?:/u/\d+)?/d/{_DRIVE_ID}"
    ),
    re.compile(rf"(?:^|/)d/{_DRIVE_ID}"),
)
_DRIVE_FOLDER = re.compile(rf"/folders/{_DRIVE_ID}")
_SLACK_CHANNEL = re.compile(r"^[CGD][A-Z0-9]{6,}$")


def dashed(notion_id: str) -> str:
    """A Notion id as the API spells it: lower-case 8-4-4-4-12."""
    plain = notion_id.replace("-", "").lower()
    return f"{plain[:8]}-{plain[8:12]}-{plain[12:16]}-{plain[16:20]}-{plain[20:]}"


def _notion_id(text: str) -> str | None:
    text = text.strip()
    for pattern in (_DASHED, _HEX32):
        found = pattern.search(text)
        if found:
            return dashed(found.group(1))
    return None


def _notion(path: str, query: dict[str, list[str]]) -> SourceLink:
    segment = unquote(path.rstrip("/").rsplit("/", 1)[-1])
    view = bool((query.get("v") or [""])[0].strip())
    peeked = _notion_id((query.get("p") or [""])[0])
    if peeked:
        # A page open in peek view (over a database view or another page): the page
        # on screen is the item, not the view behind it.
        return SourceLink("notion:nango", peeked, "page")
    name = None
    found = _DASHED.search(segment)
    if found:
        item_id = dashed(found.group(1))
    else:
        found = _HEX32.search(segment)
        if found is None:
            raise SourceLinkError(
                "This Notion link has no page or database id: copy the link from the page's "
                "Share menu (it ends in a 32-character id)."
            )
        item_id = dashed(found.group(1))
        # ``Page-Title-<id>``: the title Notion put before the id.
        name = segment[: found.start()].rstrip("-").replace("-", " ").strip() or None
    return SourceLink("notion:nango", item_id, "database" if view else "page", name=name)


def _drive(path: str, query: dict[str, list[str]]) -> SourceLink:
    folder = _DRIVE_FOLDER.search(path)
    if folder:
        return SourceLink("gdrive:nango", folder.group(1), "folder")
    for pattern in _DRIVE_FILE_PATHS:
        found = pattern.search(path)
        if found:
            return SourceLink("gdrive:nango", found.group(1), "file")
    item_id = (query.get("id") or [""])[0].strip()
    if item_id:
        return SourceLink("gdrive:nango", item_id)
    raise SourceLinkError(
        "This Google Drive link names no file or folder: use the link from the address bar "
        "(…/folders/<id> or …/file/d/<id>) or from Share > Copy link."
    )


def _slack(path: str) -> SourceLink:
    parts = [part for part in path.split("/") if part]
    channel = None
    if "archives" in parts and parts.index("archives") + 1 < len(parts):
        channel = parts[parts.index("archives") + 1]
    elif "client" in parts and parts.index("client") + 2 < len(parts):
        channel = parts[parts.index("client") + 2]
    if not channel or not _SLACK_CHANNEL.match(channel):
        raise SourceLinkError(
            "This Slack link names no channel: use the channel's Copy link "
            "(…slack.com/archives/<channel id>)."
        )
    return SourceLink("slack:nango", channel, "channel")


def parse_source_link(url: str) -> SourceLink:
    """The item a Notion, Google Drive or Slack link names.

    Raises :class:`SourceLinkError` for any other link, or one that names no item.
    """
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise SourceLinkError(f"Not a link: {url!r}. Pass the item's https:// address.")
    host = parsed.netloc.lower().split(":")[0]
    query = parse_qs(parsed.query)
    if host == "notion.so" or host.endswith((".notion.so", ".notion.site")):
        return _notion(parsed.path, query)
    if host in ("drive.google.com", "docs.google.com"):
        return _drive(parsed.path, query)
    if host == "slack.com" or host.endswith(".slack.com"):
        return _slack(parsed.path)
    raise SourceLinkError(
        f"Cannot read an item from a {host} link: --url takes Notion, Google Drive and Slack "
        "links. For another provider pass the item's id with --provider, --type and --name."
    )
