"""Analysis-only Audisor Build boundary.

This module does not create a handoff, authorize work, or execute a build.  It
checks the supplied task and original plan and returns the information needed
to revise that plan.
"""
from __future__ import annotations

import json
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from audisor.schemas.task_input import TaskInput


class BuildAnalysisError(Exception):
    def __init__(self, message: str, *, code: str = "build_analysis_failed", missing: list[str] | None = None, corrections: list[str] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.missing_inputs = list(missing or [])
        self.required_corrections = list(corrections or [])
        self.retry_prompt = ""


class Gap(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    gap_id: str = Field(min_length=1)
    location: str = Field(min_length=1)
    claim: str = Field(min_length=1)
    evidence: list[str] = Field(min_length=1)
    correction: str = Field(min_length=1)


class GapEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    result: str = Field(pattern="^(no_material_gap|material_gap_found)$")
    findings: list[Gap]


class Evaluation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    result: str = Field(pattern="^(evaluated|not_evaluable)$")
    rationale: str = Field(min_length=1)


class SuccessPredicate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    predicate_id: str = Field(min_length=1)
    observable_condition: str = Field(min_length=1)
    required_evidence: list[str] = Field(min_length=1)


class SuccessDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    statement: str = Field(min_length=1)
    predicates: list[SuccessPredicate] = Field(min_length=1)


class ValidationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    validation_id: str = Field(min_length=1)
    command: list[str] = Field(min_length=1)
    pass_condition: str = Field(min_length=1)
    fail_condition: str = Field(min_length=1)


class FixtureSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    fixture_id: str = Field(min_length=1)
    input: dict[str, Any]
    expected: dict[str, Any]


class BuildAnalysis(BaseModel):
    """The complete, non-authoritative result of one Audisor Build check."""
    model_config = ConfigDict(extra="forbid", strict=True)
    gap_evaluation: GapEvaluation
    evaluation: Evaluation
    success_definition: SuccessDefinition
    validation: list[ValidationSpec] = Field(min_length=1)
    fixtures: list[FixtureSpec] = Field(min_length=1)
    updated_original_plan: dict[str, Any]


_FIELDS = set(BuildAnalysis.model_fields)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _retry_prompt(task: Mapping[str, Any], plan: Mapping[str, Any], error: BuildAnalysisError) -> str:
    return (
        "Revise the original implementation plan and submit it for a new Audisor check. "
        "Do not implement files, execute commands, create a handoff, or claim approval.\n\n"
        "Required order: gap evaluation, plan evaluation, exact success definition, "
        "validation and fixtures, then updated original plan.\n\n"
        f"Task:\n{json.dumps(_jsonable(task), ensure_ascii=False, sort_keys=True, indent=2)}\n\n"
        f"Original plan:\n{json.dumps(_jsonable(plan), ensure_ascii=False, sort_keys=True, indent=2)}\n\n"
        f"Failure code: {error.code}\nReason: {error}\n"
        f"Missing inputs: {json.dumps(error.missing_inputs, ensure_ascii=False)}\n"
        f"Required corrections: {json.dumps(error.required_corrections, ensure_ascii=False)}\n"
        "Return only the revised original plan and its supporting evidence."
    )


def _decode(answer: Any) -> dict[str, Any]:
    if not isinstance(answer, str) or not answer.strip():
        raise BuildAnalysisError("Audisor returned no analysis", code="empty_response")
    text = answer.strip()
    if text.startswith("```"):
        raise BuildAnalysisError("Markdown framing is not allowed", code="invalid_response_framing")
    decoder = json.JSONDecoder()
    try:
        value, end = decoder.raw_decode(text)
    except json.JSONDecodeError as exc:
        raise BuildAnalysisError("Audisor returned invalid JSON", code="invalid_json", corrections=[str(exc)]) from exc
    if text[end:].strip() or not isinstance(value, dict):
        raise BuildAnalysisError("Audisor must return exactly one JSON object", code="invalid_response_framing")
    return value


def evaluate_build(*, task: Mapping[str, Any], original_plan: Mapping[str, Any], repository_context: Mapping[str, Any], worker: Any, operation_id: str) -> BuildAnalysis:
    """Invoke the analysis worker exactly once and validate analysis-only output."""
    if not task or not original_plan or not repository_context:
        missing = [name for name, value in (("task", task), ("original_plan", original_plan), ("repository_context", repository_context)) if not value]
        error = BuildAnalysisError("Audisor Build analysis inputs are incomplete", code="missing_analysis_input", missing=missing)
        error.retry_prompt = _retry_prompt(task, original_plan, error)
        raise error
    prompt = (
        "You are Audisor Build analysis only. Do not build, execute, approve, hand off, "
        "or create authority. Use only the supplied task, original plan, and repository "
        "context. Return exactly one JSON object with exactly these keys: "
        "gap_evaluation, evaluation, success_definition, validation, fixtures, "
        "updated_original_plan. Process in that order: identify evidence-backed gaps; "
        "evaluate whether the plan is coherent; define exact observable success; define "
        "executable validation and fixtures; update the original plan without replacing "
        "it. If success cannot be defined, evaluation.result must be not_evaluable and "
        "the response must explain the missing evidence in rationale. No handoff, "
        "execution_contract, authority, approval, release, or implementation fields.\n\n"
        f"task={json.dumps(_jsonable(task), ensure_ascii=False, sort_keys=True)}\n"
        f"original_plan={json.dumps(_jsonable(original_plan), ensure_ascii=False, sort_keys=True)}\n"
        f"repository_context={json.dumps(_jsonable(repository_context), ensure_ascii=False, sort_keys=True)}\n"
        "Return JSON only."
    )
    output = worker.execute(TaskInput(task_id=operation_id, prompt=prompt))
    try:
        value = _decode(getattr(output, "answer", None))
        if set(value) != _FIELDS:
            raise BuildAnalysisError("Audisor Build analysis fields are incomplete or unauthorized", code="analysis_schema_failed", corrections=[f"exact fields required: {sorted(_FIELDS)}"])
        result = BuildAnalysis.model_validate(value)
    except BuildAnalysisError as exc:
        exc.retry_prompt = _retry_prompt(task, original_plan, exc)
        raise
    except ValidationError as exc:
        error = BuildAnalysisError("Audisor Build analysis schema validation failed", code="analysis_schema_failed", corrections=[str(exc)])
        error.retry_prompt = _retry_prompt(task, original_plan, error)
        raise error from exc
    if result.gap_evaluation.result == "no_material_gap" and result.gap_evaluation.findings:
        raise BuildAnalysisError("no_material_gap cannot contain findings", code="decision_inconsistent")
    if result.gap_evaluation.result == "material_gap_found" and not result.gap_evaluation.findings:
        raise BuildAnalysisError("material_gap_found requires evidence-backed findings", code="decision_inconsistent")
    if result.evaluation.result == "not_evaluable":
        error = BuildAnalysisError("Audisor could not define a valid success evaluation", code="success_undefined", corrections=[result.evaluation.rationale])
        error.retry_prompt = _retry_prompt(task, original_plan, error)
        raise error
    if result.updated_original_plan.get("plan_id") != original_plan.get("plan_id"):
        error = BuildAnalysisError("updated plan identity does not match the original plan", code="plan_identity_mismatch", corrections=["preserve the original plan_id"])
        error.retry_prompt = _retry_prompt(task, original_plan, error)
        raise error
    return result
