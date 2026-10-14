"""An offline stand-in for the public API, for ``--dry-run`` and the tests.

It answers the routes the runner calls with the same shapes the public contract
documents, from an in-memory store: writes stage proposals, accepting a
proposal applies it, compounding turns an imported conversation's assistant
turns into a page, and research answers from the pages whose words overlap the
question.

A dry run exercises the benchmark harness end to end -- case loading, seeding,
compounding, polling, scoring, reporting -- through the real SDK and its real
HTTP transport. It says nothing about Beakr's quality: the fake is honest by
construction (it always notes that its record may be incomplete and it always
uses the latest version of a page).
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

DRY_RUN_BASE_URL = "https://beakr-bench.dry-run"
_WORD = re.compile(r"[a-z0-9][a-z0-9\-\.]{2,}")
_STOPWORDS = frozenset(
    {
        "what",
        "which",
        "when",
        "where",
        "does",
        "the",
        "and",
        "for",
        "with",
        "from",
        "that",
        "this",
        "are",
        "was",
        "were",
        "has",
        "have",
        "who",
        "how",
        "all",
        "list",
        "every",
    }
)


@dataclass
class _Project:
    name: str
    pages: dict[str, list[dict[str, Any]]] = field(default_factory=dict)


@dataclass
class FakeEngine:
    projects: dict[str, _Project] = field(default_factory=dict)
    proposals: dict[str, dict[str, Any]] = field(default_factory=dict)
    conversations: dict[str, dict[str, Any]] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)

    # -- helpers ---------------------------------------------------------------

    def _stage(self, project_id: str, title: str, sections: list[dict[str, Any]]) -> dict:
        proposal_id = str(uuid.uuid4())
        self.proposals[proposal_id] = {
            "project_id": project_id,
            "title": title,
            "sections": sections,
            "status": "pending",
        }
        # The real write door's shape: prose plus the outcome sidecar, with no
        # proposal id (a staged write commits nothing yet).
        return {
            "content": [
                {"type": "text", "text": f'Proposed new page: "{title}". PROPOSAL pending.'}
            ],
            "outcome": {
                "protocol": "beakr.wiki_tool_outcome.v1",
                "status": "success",
                "error_code": None,
                "committed_page_ids": [],
                "committed_revisions": {},
                "commit_ids": [],
            },
        }

    def _answer(self, project_id: str, question: str) -> dict[str, Any]:
        terms = {w for w in _WORD.findall(question.lower()) if w not in _STOPWORDS}
        project = self.projects.get(project_id)
        hits: list[tuple[str, str]] = []
        for title, sections in (project.pages if project else {}).items():
            text = " ".join(f"{s['title']}: {s['body']}" for s in sections)
            if terms & set(_WORD.findall(text.lower())) or terms & set(
                _WORD.findall(title.lower())
            ):
                hits.append((title, re.sub(r"\{\{[^}]+\}\}", "", text).strip()))
        if not hits:
            return {
                "answer": "I found no records about this in the project.",
                "sources": [],
            }
        body = " ".join(f"{title}: {text}" for title, text in hits)
        return {
            "answer": f"{body} Based on the records available; the record may be incomplete.",
            "sources": [{"title": title, "kind": "page"} for title, _ in hits],
        }

    def _session_context(self, project_id: str | None) -> dict[str, Any]:
        project = self.projects.get(project_id or "")
        lines = []
        for title, sections in (project.pages if project else {}).items():
            text = " ".join(f"{s['title']}: {s['body']}" for s in sections)
            plain = re.sub(r"\{\{[^}]+\}\}", "", text).strip()
            lines.append(f"- {title}: {plain}")
        content = "\n".join(lines) or "No records for this project yet."
        return {
            "format": "markdown",
            "content": content,
            "blocks": [{"name": "entities", "content": content}],
            "sources": [],
        }

    # -- routes ----------------------------------------------------------------

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method
        self.calls.append((method, path))
        body = json.loads(request.content) if request.content else {}

        if method == "POST" and path == "/v1/projects":
            project_id = str(uuid.uuid4())
            self.projects[project_id] = _Project(name=body["name"])
            return httpx.Response(201, json={"id": project_id, "name": body["name"]})
        if method == "DELETE" and path.startswith("/v1/projects/"):
            self.projects.pop(path.rsplit("/", 1)[-1], None)
            return httpx.Response(204)
        if method == "POST" and path == "/v1/knowledge/wiki/write":
            title = body.get("title") or body.get("page")
            return httpx.Response(200, json=self._stage(body["scope"], title, body["sections"]))
        if method == "POST" and path.endswith("/accept"):
            proposal = self.proposals.get(path.split("/")[-2])
            if proposal is None:
                return httpx.Response(404, json={"error": {"code": "not_found"}})
            if proposal["status"] == "pending":
                project = self.projects[proposal["project_id"]]
                project.pages[proposal["title"]] = proposal["sections"]
                proposal["status"] = "accepted"
            return httpx.Response(200, json={"status": "accepted"})
        if method == "GET" and path == "/v1/knowledge/wiki/proposals":
            project_id = request.url.params.get("project_id")
            pending = [
                {"id": pid, "page_title": p["title"]}
                for pid, p in self.proposals.items()
                if p["project_id"] == project_id and p["status"] == "pending"
            ]
            return httpx.Response(
                200, json={"data": pending, "next_cursor": None, "has_more": False}
            )
        if method == "GET" and path.startswith("/v1/knowledge/wiki/proposals/"):
            proposal_id = path.rsplit("/", 1)[-1]
            proposal = self.proposals.get(proposal_id)
            if proposal is None:
                return httpx.Response(404, json={"error": {"code": "not_found"}})
            return httpx.Response(200, json={"id": proposal_id, **proposal})
        if method == "POST" and path == "/v1/knowledge/wiki/command":
            project = self.projects.get(body.get("scope") or "")
            title = body.get("page") or body.get("card") or ""
            sections = (project.pages if project else {}).get(title)
            if body.get("command") != "cat" or sections is None:
                text = f"No page {title!r}."
            else:
                text = "\n".join(f"## {s['title']}\n{s['body']}" for s in sections)
            return httpx.Response(200, json={"content": [{"type": "text", "text": text}]})
        if method == "POST" and path == "/v1/conversations/import":
            conversation_id = str(uuid.uuid4())
            self.conversations[conversation_id] = {**body, "compound_status": None}
            return httpx.Response(
                200,
                json={
                    "conversation_id": conversation_id,
                    "created": True,
                    "message_count": len(body.get("messages", [])),
                },
            )
        if method == "POST" and path == "/v1/compound":
            conversation = self.conversations[body["conversation_id"]]
            said = " ".join(
                m["content"] for m in conversation.get("messages", []) if m["role"] == "assistant"
            )
            self._stage(
                conversation["project_id"],
                f"Imported session {conversation['session_id']}",
                [{"title": "What was established", "body": said}],
            )
            # The engine's vocabulary (imported_conversations.compound_status).
            conversation["compound_status"] = "done"
            return httpx.Response(202, json={"status": "queued"})
        if method == "GET" and path.startswith("/v1/conversations/"):
            conversation = self.conversations.get(path.rsplit("/", 1)[-1])
            if conversation is None:
                return httpx.Response(404, json={"error": {"code": "not_found"}})
            return httpx.Response(200, json={"compound_status": conversation["compound_status"]})
        if method == "POST" and path == "/v1/knowledge/research":
            return httpx.Response(200, json=self._answer(body.get("project_id"), body["query"]))
        if method == "POST" and path == "/v1/context":
            if not body.get("question"):
                # Session-start context (what agent hooks inject): no question,
                # so it carries the project's knowledge rather than an answer.
                return httpx.Response(200, json=self._session_context(body.get("project_id")))
            answer = self._answer(body.get("project_id"), body.get("question") or "")
            return httpx.Response(
                200,
                json={
                    "format": "markdown",
                    "content": answer["answer"],
                    "blocks": [{"name": "entities", "content": answer["answer"]}],
                    "sources": answer["sources"],
                },
            )
        return httpx.Response(404, json={"error": {"code": "not_found", "message": path}})


def dry_run_client(engine: FakeEngine | None = None):
    """A real ``beakr_sdk.Beakr`` whose HTTP goes to an in-memory fake engine."""
    from beakr_sdk import Beakr

    engine = engine or FakeEngine()
    http = httpx.Client(transport=httpx.MockTransport(engine.handle))
    client = Beakr(api_key="bk_test_dry_run", base_url=DRY_RUN_BASE_URL, http_client=http)
    client._sleep = lambda _seconds: None
    return client, engine
