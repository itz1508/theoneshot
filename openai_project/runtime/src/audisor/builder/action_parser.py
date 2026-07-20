"""Strict Audisor envelope normalization and bounded action parsing."""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from audisor.builder.evidence import (
    contains_environment_secret,
    sanitize_text,
    sha256_bytes,
)
from audisor.schemas.execution import SanitizedWorkerOutput, WorkerActionPlan
from audisor.schemas.task_output import TaskOutput

MAX_WORKER_ANSWER_BYTES = 1_000_000


class ActionPlanError(RuntimeError):
    """The worker returned an unusable internal action plan."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> None:
    raise ValueError(f"non-finite JSON value: {value}")


def normalize_worker_result(raw: object, expected_task_id: str) -> TaskOutput:
    """Require the normalized typed provider envelope."""
    try:
        if isinstance(raw, TaskOutput):
            output = raw
        else:
            raise ActionPlanError("Worker returned an unusable task result")
    except ValidationError:
        raise ActionPlanError("Worker returned an unusable task result") from None
    if output.task_id != expected_task_id:
        raise ActionPlanError("Worker returned a mismatched task_id")
    return output


def parse_action_plan(output: TaskOutput) -> tuple[WorkerActionPlan, SanitizedWorkerOutput]:
    answer_bytes = output.answer.encode("utf-8", errors="backslashreplace")
    if not answer_bytes or len(answer_bytes) > MAX_WORKER_ANSWER_BYTES:
        raise ActionPlanError("Worker action response has an invalid size")
    if contains_environment_secret(output.answer):
        raise ActionPlanError("Worker action response contains protected data")
    try:
        payload = json.loads(
            output.answer,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
        )
    except (json.JSONDecodeError, TypeError, ValueError):
        raise ActionPlanError("Worker returned malformed action JSON") from None
    if not isinstance(payload, dict):
        raise ActionPlanError("Worker returned an unusable action plan")
    try:
        plan = WorkerActionPlan.model_validate(payload)
    except ValidationError:
        raise ActionPlanError("Worker returned an invalid action plan") from None
    excerpt, truncated = sanitize_text(output.answer, limit=8192)
    sanitized = SanitizedWorkerOutput(
        task_id=output.task_id,
        answer_sha256=sha256_bytes(answer_bytes),
        answer_bytes=len(answer_bytes),
        answer_excerpt=excerpt,
        excerpt_truncated=truncated,
    )
    return plan, sanitized


def sanitized_unusable_output(raw: object, expected_task_id: str) -> SanitizedWorkerOutput | None:
    """Persist bounded failure evidence without serializing provider metadata."""
    if isinstance(raw, TaskOutput):
        answer = raw.answer
        task_id = raw.task_id
    else:
        return None
    encoded = answer.encode("utf-8", errors="backslashreplace")
    excerpt, truncated = sanitize_text(answer, limit=8192)
    return SanitizedWorkerOutput(
        task_id=task_id if task_id else expected_task_id,
        answer_sha256=sha256_bytes(encoded),
        answer_bytes=len(encoded),
        answer_excerpt=excerpt,
        excerpt_truncated=truncated,
    )
