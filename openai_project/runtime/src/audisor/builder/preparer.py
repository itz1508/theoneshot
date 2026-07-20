"""Planning-worker orchestration for Phase 2A build preparation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from audisor.builder.coverage import CoverageValidationError, validate_plan_coverage
from audisor.builder.dependencies import (
    DependencyValidationError,
    deterministic_topological_order,
)
from audisor.builder.skill_renderer import SkillRenderingError, render_skills
from audisor.builder.store import BuildStore
from audisor.routing.router import ProviderRouter
from audisor.schemas.build import BuildPlan, BuildRequest
from audisor.schemas.task_input import TaskInput
from audisor.schemas.task_output import TaskOutput
from audisor.workers.base import (
    ProviderCapabilityError,
    ProviderInvalidResponseError,
)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> None:
    raise ValueError(f"non-finite JSON value: {value}")


@dataclass
class BuildPreparer:
    """Prepare and persist one build through the selected planning worker."""

    router: ProviderRouter
    store: BuildStore

    @staticmethod
    def planning_prompt(request: BuildRequest) -> str:
        """Construct the complete strict-JSON planning instruction."""
        instruction_json = json.dumps(request.instruction, ensure_ascii=False)
        return "\n".join(
            [
                "Prepare an Audisor build plan as one strict JSON object.",
                "Return JSON only: no Markdown fences, commentary, duplicate keys, NaN, or Infinity.",
                f"build_id must be exactly {json.dumps(request.build_id)}.",
                f"The build instruction is the JSON string {instruction_json}.",
                "Use exactly these top-level fields: build_id, status, gaps, tasks.",
                "status must be ready or blocked.",
                "A ready plan has zero gaps and at least one task.",
                "A blocked plan has at least one specific gap and zero tasks.",
                "Every task has exactly task_id, title, depends_on, prompt, expected_outputs, and validation.",
                "Task IDs and dependencies must be safe identifiers.",
                "expected_outputs must be a non-empty list of unique safe relative paths the task must create or modify.",
                "validation must be a non-empty list of deferred executable validation metadata.",
                "Each validation object has exactly argv, working_directory, acceptable_exit_codes, and timeout_seconds.",
                "argv is a non-empty argument list; working_directory is a safe relative path or '.'.",
                "acceptable_exit_codes is a non-empty list of unique integers from 0 through 255; timeout_seconds is 1 through 300.",
                "Validation commands are recorded and hashed but are not executed in this phase.",
                "Each ready task prompt must contain these Markdown headings exactly once and in order:",
                "## Objective",
                "## Inputs and repository paths",
                "## Required work",
                "## Ordered steps",
                "## Expected output",
                "## Validation",
                "## Evidence to return",
                "Every section must be specific and non-placeholder.",
                "Ordered steps must contain at least one numbered step.",
                "If no external path is needed, say that explicitly instead of inventing one.",
            ]
        )

    @staticmethod
    def _parse_plan(raw_result: str) -> BuildPlan:
        cleaned = raw_result.strip()
        if cleaned.startswith("```") and cleaned.endswith("```"):
            lines = cleaned.splitlines()
            if len(lines) < 3 or lines[0].strip().casefold() not in {"```", "```json"}:
                raise ProviderInvalidResponseError(
                    "Selected provider returned an invalid build plan",
                    internal_detail="plan_json=framing",
                )
            cleaned = "\n".join(lines[1:-1]).strip()
        try:
            payload = json.loads(
                cleaned,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_non_finite,
            )
        except (json.JSONDecodeError, TypeError, ValueError):
            raise ProviderInvalidResponseError(
                "Selected provider returned an invalid build plan",
                internal_detail="plan_json=malformed",
            ) from None
        if not isinstance(payload, dict):
            raise ProviderInvalidResponseError(
                "Selected provider returned an invalid build plan",
                internal_detail="plan_json=not_object",
            )
        try:
            return BuildPlan.model_validate(payload)
        except ValidationError:
            raise ProviderInvalidResponseError(
                "Selected provider returned an invalid build plan",
                internal_detail="plan_schema=invalid",
            ) from None

    def prepare(self, request: BuildRequest) -> BuildPlan:
        """Validate all planner output before atomically publishing artifacts."""
        provider = self.router.select_provider()
        if not provider.capabilities().text:
            raise ProviderCapabilityError(
                "Selected provider does not support text tasks",
                internal_detail="required=text",
            )
        self.store.assert_available(request.build_id)
        task = TaskInput(task_id=request.build_id, prompt=self.planning_prompt(request))
        output = provider.execute(task)
        if not isinstance(output, TaskOutput) or output.task_id != task.task_id:
            raise ProviderInvalidResponseError(
                "Selected provider returned an invalid build plan",
                internal_detail="task_output=invalid",
            )
        plan = self._parse_plan(output.answer)
        if plan.build_id != request.build_id:
            raise ProviderInvalidResponseError(
                "Selected provider returned an invalid build plan",
                internal_detail="build_id=mismatch",
            )

        skills = []
        if plan.status == "ready":
            try:
                ordered_tasks = deterministic_topological_order(plan.tasks)
                validate_plan_coverage(ordered_tasks)
                plan = plan.model_copy(update={"tasks": ordered_tasks})
                skills = render_skills(plan.build_id, ordered_tasks)
            except DependencyValidationError:
                raise ProviderInvalidResponseError(
                    "Selected provider returned an invalid build plan",
                    internal_detail="dependencies=invalid",
                ) from None
            except CoverageValidationError:
                raise ProviderInvalidResponseError(
                    "Selected provider returned an invalid build plan",
                    internal_detail="coverage=incomplete",
                ) from None
            except SkillRenderingError:
                raise ProviderInvalidResponseError(
                    "Selected provider returned an invalid build plan",
                    internal_detail="task=unrenderable",
                ) from None

        self.store.publish(request, plan, skills)
        return plan
