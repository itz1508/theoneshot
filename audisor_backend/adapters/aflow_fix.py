"""A-Flow Fix adapter. The injected agent is advisory and called once."""

from collections.abc import Callable
import json
from typing import Any

from audisor_backend.schemas.fix.constants import MAX_AFLOW_INVOCATIONS_PER_OPERATION
from audisor_backend.schemas.fix.models import (
    AFlowOutputs, EvaluatedPlan, FindingsList, FixScopedManifest, ImplementationPlan,
    PlanStep,
)


class FixLocalInvocationError(RuntimeError):
    """Fail-closed error from the Fix-local model boundary."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class FixIgnitionResult:
    """Small Fix-specific result; it never carries execution authority."""

    def __init__(self, plan: ImplementationPlan, *, accepted: bool):
        self.candidate_plan = plan
        self.execution_contract = None
        self.implementation_eligible = accepted


def _one_json_object(answer: object) -> dict[str, Any]:
    if not isinstance(answer, str) or not answer.strip():
        raise FixLocalInvocationError("empty_response", "Fix model returned no content")
    text = answer.strip()
    if text.startswith("```") or text.endswith("```"):
        raise FixLocalInvocationError("invalid_response_framing", "Markdown framing is not allowed")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise FixLocalInvocationError("invalid_json", "Fix model returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise FixLocalInvocationError("invalid_response_framing", "Fix model must return one JSON object")
    return value


def _plan_from_json(value: object, original: ImplementationPlan, findings: FindingsList, manifest: FixScopedManifest) -> ImplementationPlan:
    if not isinstance(value, dict):
        raise FixLocalInvocationError("schema_invalid", "Fix model plan is not an object")
    raw_steps = value.get("steps")
    if not isinstance(raw_steps, list):
        raise FixLocalInvocationError("schema_invalid", "Fix model plan steps are missing")
    steps: list[PlanStep] = []
    finding_ids = {finding.id for finding in findings}
    for item in raw_steps:
        if not isinstance(item, dict) or not all(isinstance(item.get(key), str) and item[key] for key in ("id", "action", "target_file", "originating_finding_id")):
            raise FixLocalInvocationError("schema_invalid", "Fix model emitted a malformed plan step")
        if item["originating_finding_id"] not in finding_ids or item["target_file"] not in manifest.files:
            raise FixLocalInvocationError("scope_violation", "Fix model step is outside the finding scope")
        steps.append(PlanStep(item["id"], item["action"], item["target_file"], item["originating_finding_id"], item.get("acceptance_criterion")))
    if not steps:
        raise FixLocalInvocationError("schema_invalid", "Fix model emitted no repair steps")
    return ImplementationPlan(steps, sorted({step.target_file for step in steps}), True, [])


def invoke_local_fix(worker: Any, plan: ImplementationPlan, findings: FindingsList, manifest: FixScopedManifest) -> FixIgnitionResult:
    """Invoke the configured local model once for Fix analysis only."""
    from audisor.schemas.task_input import TaskInput

    worker.structured_output = True
    prompt = {
        "instruction": "Evaluate this scan-identified Fix plan as advisory analysis only. Return exactly one JSON object with status, plan, and gap_corrections_applied. Do not score findings, choose ready or isolated items, execute commands, mutate files, approve changes, or produce a final result. Repair only the listed findings.",
        "plan": {"steps": [step.__dict__ for step in plan.steps], "target_files": plan.target_files},
        "findings": [finding.__dict__ for finding in findings],
        "manifest": manifest.__dict__,
    }
    try:
        response = worker.execute(TaskInput(task_id="aflow-fix", prompt=json.dumps(prompt, sort_keys=True)))
    except Exception as exc:
        raise FixLocalInvocationError("provider_failed", "Fix local provider request failed") from exc
    value = _one_json_object(getattr(response, "answer", None))
    if set(value) != {"status", "plan", "gap_corrections_applied"} or value["status"] not in {"accepted", "rejected"}:
        raise FixLocalInvocationError("schema_invalid", "Fix model response fields are invalid")
    corrected = _plan_from_json(value["plan"], plan, findings, manifest)
    if int(value["gap_corrections_applied"]) < 0:
        raise FixLocalInvocationError("decision_inconsistent", "Fix gap correction count is invalid")
    return FixIgnitionResult(corrected, accepted=value["status"] == "accepted")


class AFlowFixAdapter:
    def __init__(self, agent: Callable[[dict[str, Any]], dict[str, Any]]):
        self.agent = agent
        self.invocations = 0

    def ignite(self, plan: ImplementationPlan, findings: FindingsList, manifest: FixScopedManifest) -> EvaluatedPlan:
        # A-Flow may qualify or correct the proposed plan, but it never owns
        # completeness scoring or the ready/isolated partition.
        if self.invocations >= MAX_AFLOW_INVOCATIONS_PER_OPERATION:
            raise RuntimeError("MAX_AFLOW_INVOCATIONS_PER_OPERATION exceeded")
        self.invocations += 1
        response = self.agent({"plan": plan, "findings": findings, "manifest": manifest})
        if not isinstance(response, dict) or response.get("status") not in ("accepted", "rejected"):
            raise ValueError("malformed A-Flow Fix output")
        corrected = response.get("plan", plan)
        if not isinstance(corrected, ImplementationPlan):
            raise TypeError("agent must return a validated ImplementationPlan")
        return EvaluatedPlan(corrected, int(response.get("gap_corrections_applied", 0)), response["status"])

    @staticmethod
    def validate_outputs(outputs: AFlowOutputs, findings: FindingsList) -> None:
        if not outputs.success_definition.covers(findings):
            raise ValueError("success definition does not cover every finding")
        if any(not check.check or not check.expected_result for check in outputs.success_definition.finding_checks):
            raise ValueError("finding checks must be concrete")
