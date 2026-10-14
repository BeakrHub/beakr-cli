"""Workflows: templates, saved configurations and their runs, over unlisted routes.

Example::

    config = beakr.workflows.create_config(template_id, inputs={"topic": "Q3 churn"})
    run = beakr.workflows.run_config(config["id"])
    status = beakr.workflows.get_run(run["id"])
"""

from __future__ import annotations

from typing import Any

from beakr_sdk._calls import clean_body
from beakr_sdk._resource import M
from beakr_sdk._typing import returns, returns_all
from beakr_sdk.resources._base import Resource, as_list

from beakr_cli._private_api._generated import operations as ops


class Workflows(Resource[M]):
    @returns_all(ops.build_workflows_list_templates)
    def templates(self, *, category: str | None = None, project_id: str | None = None) -> Any:
        return self._list_all(
            ops.build_workflows_list_templates(category=category, project_id=project_id)
        )

    @returns_all(ops.build_workflows_list_configs)
    def configs(self, *, template_id: str | None = None, is_saved: bool | None = None) -> Any:
        return self._list_all(
            ops.build_workflows_list_configs(template_id=template_id, is_saved=is_saved)
        )

    def generate_template(
        self,
        description: str,
        *,
        example_output: str | None = None,
        example_output_type: str | None = None,
        example_output_name: str | None = None,
    ) -> Any:
        """Beakr drafts a template definition from a description; nothing is saved.

        ``example_output`` is a base64 example of the output you want.
        """
        body = clean_body(
            {
                "description": description,
                "example_output": example_output,
                "example_output_type": example_output_type,
                "example_output_name": example_output_name,
            }
        )
        return self._run(ops.build_workflows_ai_generate_workflow(body=body))

    def refine_template(
        self,
        current_template: dict[str, Any],
        instruction: str,
        *,
        original_description: str | None = None,
    ) -> Any:
        """Beakr revises a template definition from an instruction; nothing is saved."""
        body = clean_body(
            {
                "current_template": current_template,
                "instruction": instruction,
                "original_description": original_description,
            }
        )
        return self._run(ops.build_workflows_ai_refine_template(body=body))

    def create_config(
        self,
        template_id: str,
        *,
        name: str | None = None,
        inputs: dict[str, Any] | None = None,
        schedule_cron: str | None = None,
        schedule_timezone: str | None = None,
        schedule_enabled: bool | None = None,
        skill_ids: list[str] | None = None,
        output_location_type: str | None = None,
        output_location_id: str | None = None,
        is_saved: bool = True,
        **fields: Any,
    ) -> Any:
        """Save a workflow: a template, its inputs, where outputs go, and a schedule.

        ``skill_ids`` makes the run load those skills (spec: a workflow is a skill
        plus a trigger plus a scope); ``output_location_type="project"`` with the
        project id files outputs in that project.
        """
        meta = dict(fields.pop("meta", None) or {})
        if skill_ids is not None:
            meta["skill_ids"] = list(skill_ids)
        body = clean_body(
            {
                "template_id": template_id,
                "name": name,
                "inputs": inputs or {},
                "schedule_cron": schedule_cron,
                "schedule_timezone": schedule_timezone,
                "schedule_enabled": schedule_enabled,
                "output_location_type": output_location_type,
                "output_location_id": output_location_id,
                "is_saved": is_saved,
                "meta": meta or None,
                **fields,
            }
        )
        return self._run(ops.build_workflows_create_config(body=body))

    @returns(ops.build_workflows_update_config)
    def update_config(self, config_id: str, **fields: Any) -> Any:
        """Change a saved workflow (``name``, ``inputs``, ``schedule_cron`` ...)."""
        return self._run(ops.build_workflows_update_config(config_id, body=clean_body(fields)))

    def schedule(
        self,
        config_id: str,
        cron: str,
        *,
        timezone: str | None = None,
        enabled: bool = True,
    ) -> Any:
        """Run a saved workflow on a cron schedule (``enabled=False`` pauses it)."""
        return self.update_config(
            config_id, schedule_cron=cron, schedule_timezone=timezone, schedule_enabled=enabled
        )

    def preview_schedule(
        self, cron: str, *, timezone: str | None = None, count: int | None = None
    ) -> Any:
        """The next times a cron expression fires, to check it before saving."""
        body = clean_body({"cron": cron, "timezone": timezone, "count": count})
        return self._run(ops.build_workflows_preview_schedule(body=body))

    def config_runs(
        self, config_id: str, *, status: str | None = None, limit: int | None = None
    ) -> Any:
        return self._run_then(
            ops.build_workflows_list_runs_for_config(config_id, status=status, limit=limit),
            lambda r: as_list(r, "data", "runs"),
        )

    @returns(ops.build_workflows_start_run)
    def run_config(self, config_id: str, *, input_overrides: dict[str, Any] | None = None) -> Any:
        return self._run(
            ops.build_workflows_start_run(
                config_id, body={"input_overrides": input_overrides or {}}
            )
        )

    def runs(
        self,
        *,
        config_id: str | None = None,
        status: str | None = None,
        limit: int | None = None,
    ) -> Any:
        return self._run_then(
            ops.build_workflows_list_runs(config_id=config_id, status=status, limit=limit),
            lambda r: as_list(r, "data", "runs"),
        )

    @returns(ops.build_workflows_get_run)
    def get_run(self, run_id: str) -> Any:
        return self._run(ops.build_workflows_get_run(run_id))
