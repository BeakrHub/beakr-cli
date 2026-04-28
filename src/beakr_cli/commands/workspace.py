"""Workspace discovery commands: projects, profiles."""

from __future__ import annotations

import typer

from beakr_cli.client import get_client
from beakr_cli.output import (
    console,
    err_console,
    is_piped,
    print_json,
    print_table,
)

app = typer.Typer(help="Discover projects and profiles in your org.")


# ---------------------------------------------------------------------------
# projects
# ---------------------------------------------------------------------------


@app.command()
def projects(
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
) -> None:
    """List all projects in your organization."""
    # Bypass auto-scope — list all projects
    with get_client() as c:
        resp = c.get("/v1/projects")
        resp.raise_for_status()
        data = resp.json()
    project_list = data if isinstance(data, list) else data.get("projects", data)

    if json_out or is_piped():
        print_json(project_list)
        return

    if not project_list:
        err_console.print("No projects found.")
        return

    columns = ["Name", "ID", "Description"]
    rows = []
    for p in project_list:
        rows.append([
            p.get("name", ""),
            str(p.get("id", "")),
            (p.get("description", "") or "")[:50],
        ])
    print_table(columns, rows, title="Projects")


# ---------------------------------------------------------------------------
# profiles
# ---------------------------------------------------------------------------


@app.command()
def profiles(
    json_out: bool = typer.Option(False, "--json", "-j", help="Output raw JSON."),
) -> None:
    """List knowledge profiles (org and project)."""
    profile_list: list[dict] = []

    with get_client() as c:
        # Fetch org profile
        try:
            resp = c.get("/v1/knowledge/profiles/org")
            resp.raise_for_status()
            org_profile = resp.json()
            if org_profile:
                org_profile["_profile_type"] = "org"
                profile_list.append(org_profile)
        except Exception:
            pass

        # Fetch projects, then each project profile
        try:
            resp = c.get("/v1/projects")
            resp.raise_for_status()
            projects_data = resp.json()
            projects = projects_data if isinstance(projects_data, list) else projects_data.get("projects", [])
            for p in projects:
                pid = p.get("id")
                if not pid:
                    continue
                try:
                    resp = c.get(f"/v1/knowledge/profiles/project/{pid}")
                    resp.raise_for_status()
                    pp = resp.json()
                    if pp:
                        pp["_profile_type"] = "project"
                        profile_list.append(pp)
                except Exception:
                    pass
        except Exception:
            pass

    if json_out or is_piped():
        print_json(profile_list)
        return

    if not profile_list:
        err_console.print("No profiles found.")
        return

    columns = ["Name", "Type", "ID", "Focus Areas"]
    rows = []
    for p in profile_list:
        focus = ", ".join(p.get("focus_areas", []))[:40]
        rows.append([
            p.get("name", ""),
            p.get("_profile_type", ""),
            str(p.get("id", "")),
            focus,
        ])
    print_table(columns, rows, title="Knowledge Profiles")
