"""Workspace discovery commands: projects, profiles."""

from __future__ import annotations

import typer

from beakr_cli.output import (
    err_console,
    machine_output,
    print_table,
)
from beakr_cli.sdk import get_sdk

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
    with get_sdk() as sdk:
        project_list = sdk.projects.list()

    if machine_output(project_list, json_out=json_out):
        return

    if not project_list:
        err_console.print("No projects found.")
        return

    columns = ["Name", "ID", "Description"]
    rows = []
    for p in project_list:
        rows.append(
            [
                p.get("name", ""),
                str(p.get("id", "")),
                (p.get("description", "") or "")[:50],
            ]
        )
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

    with get_sdk() as sdk:
        # Fetch org profile
        try:
            org_profile = sdk.profile.org()
            if org_profile:
                org_profile["_profile_type"] = "org"
                profile_list.append(org_profile)
        except Exception:
            pass

        # Fetch projects, then each project profile
        try:
            for p in sdk.projects.list():
                pid = p.get("id")
                if not pid:
                    continue
                try:
                    pp = sdk.profile.project(pid)
                    if pp:
                        pp["_profile_type"] = "project"
                        profile_list.append(pp)
                except Exception:
                    pass
        except Exception:
            pass

    if machine_output(profile_list, json_out=json_out):
        return

    if not profile_list:
        err_console.print("No profiles found.")
        return

    columns = ["Name", "Type", "ID", "Focus Areas"]
    rows = []
    for p in profile_list:
        focus = ", ".join(p.get("focus_areas", []))[:40]
        rows.append(
            [
                p.get("name", ""),
                p.get("_profile_type", ""),
                str(p.get("id", "")),
                focus,
            ]
        )
    print_table(columns, rows, title="Knowledge Profiles")
