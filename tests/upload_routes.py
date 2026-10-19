"""Mocked ``/v1/uploads`` routes for tests that upload files.

Every upload goes through the resumable flow (create, state, part URLs, PUT,
complete). Each created upload gets its own id; completing it answers with an
artifact whose id is ``art-<file name>``, so tests can tell files apart.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import respx

S3 = "https://bucket.s3.test"


def mock_resumable_uploads(
    api: str, *, folders: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Register the routes on the active respx router; returns them plus ``created``.

    ``created`` lists each ``POST /v1/uploads`` body in order. ``folders`` seeds the
    project's existing folders (``{"id", "name", "parent_folder_id"}``); the folder
    routes behave like the API: ``GET /v1/folders`` lists one level, and creating a
    folder whose name is taken under the same parent answers 400.
    """
    created: list[dict[str, Any]] = []
    store: list[dict[str, Any]] = [dict(f) for f in folders or []]

    def list_folders(request: httpx.Request) -> httpx.Response:
        parent = request.url.params.get("parent_folder_id")
        rows = [f for f in store if f.get("parent_folder_id") == parent]
        return httpx.Response(200, json={"data": rows, "next_cursor": None, "has_more": False})

    def create_folder(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        parent = body.get("parent_folder_id")
        if any(f["name"] == body["name"] and f.get("parent_folder_id") == parent for f in store):
            message = f"A folder named '{body['name']}' already exists at this location"
            return httpx.Response(400, json={"detail": {"code": "bad_request", "message": message}})
        taken = {f["id"] for f in store}
        folder_id = "fold-" + body["name"]
        while folder_id in taken:
            folder_id += "+"
        store.append({"id": folder_id, "name": body["name"], "parent_folder_id": parent})
        return httpx.Response(201, json={"id": folder_id})

    def state(upload_id: str) -> dict[str, Any]:
        body = created[int(upload_id.removeprefix("up-")) - 1]
        return {
            "id": upload_id,
            "status": "open",
            "project_id": body["target_project_id"],
            "content_length": body["content_length"],
            "part_size": 5 * 1024 * 1024,
            "part_count": 1,
            "uploaded_parts": [],
        }

    def create(request: httpx.Request) -> httpx.Response:
        created.append(json.loads(request.content))
        return httpx.Response(201, json=state(f"up-{len(created)}"))

    def get(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=state(request.url.path.rsplit("/", 1)[-1]))

    def parts(request: httpx.Request) -> httpx.Response:
        upload_id = request.url.path.split("/")[-2]
        return httpx.Response(
            200,
            json={
                "upload_id": upload_id,
                "parts": [{"part_number": 1, "url": f"{S3}/{upload_id}?part=1"}],
            },
        )

    def complete(request: httpx.Request) -> httpx.Response:
        upload_id = request.url.path.split("/")[-2]
        title = json.loads(request.content).get("title") or upload_id
        return httpx.Response(
            200,
            json={
                "upload": {**state(upload_id), "status": "completed"},
                "artifact": {"id": f"art-{title}", "title": title},
                "folders": [],
                "job_id": f"job-{upload_id}",
                "status_url": f"/v1/jobs/job-{upload_id}",
            },
        )

    return {
        "created": created,
        "create": respx.post(f"{api}/v1/uploads").mock(side_effect=create),
        "get": respx.get(url__regex=rf"{api}/v1/uploads/up-\d+$").mock(side_effect=get),
        "parts": respx.post(url__regex=rf"{api}/v1/uploads/up-\d+/parts").mock(side_effect=parts),
        "put": respx.put(url__startswith=S3).respond(200, headers={"ETag": '"etag-1"'}),
        "complete": respx.post(url__regex=rf"{api}/v1/uploads/up-\d+/complete").mock(
            side_effect=complete
        ),
        "folders": store,
        "folder": respx.post(f"{api}/v1/folders").mock(side_effect=create_folder),
        "list_folders": respx.get(f"{api}/v1/folders").mock(side_effect=list_folders),
    }
