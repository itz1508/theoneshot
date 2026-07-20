"""A-Flow Fix adapter. The injected agent is advisory and called once."""

from collections.abc import Callable
import json
from typing import Any

from audisor_backend.schemas.fix.constants import MAX_AFLOW_INVOCATIONS_PER_OPERATION
from audisor_backend.schemas.fix.models import (
    AFlowOutputs, EvaluatedPlan, FindingCheck, FindingsList, FixScopedManifest,
    ImplementationPlan, PlanStep, SuccessDefinition, ValidationSpec,
)


class FixLocalInvocationError(RuntimeError):
    """Fail-closed error from the Fix-local model boundary."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class FixIgnitionResult:
    """Small Fix-specific result; it never carries execution authority."""

    def __init__(self, plan: ImplementationPlan, *, accepted: bool, success_definition: SuccessDefinition | None = None, verification_grounding: Any = None):
        self.candidate_plan = plan
        self.execution_contract = None
        self.implementation_eligible = accepted
        self.success_definition = success_definition
        self.verification_grounding = verification_grounding


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


def _success_definition_from_json(value: object, findings: FindingsList, manifest: FixScopedManifest) -> SuccessDefinition:
    """Parse and validate the success_definition from the single model response.

    Reuses the existing FindingCheck, ValidationSpec, and SuccessDefinition
    schema types.  Validates that every finding is covered, every finding_id
    references an actual finding, resolution_method is valid, checks are
    concrete, validations have stable IDs and concrete commands, and
    success_rule is non-empty.
    """
    if not isinstance(value, dict):
        raise FixLocalInvocationError("schema_invalid", "Fix model success_definition is not an object")

    # Parse finding_checks
    raw_checks = value.get("finding_checks")
    if not isinstance(raw_checks, list) or not raw_checks:
        raise FixLocalInvocationError("verification_contract_incomplete", "success_definition.finding_checks is missing or empty")
    finding_ids = {finding.id for finding in findings}
    checks: list[FindingCheck] = []
    for item in raw_checks:
        if not isinstance(item, dict):
            raise FixLocalInvocationError("schema_invalid", "finding_check is not an object")
        finding_id = item.get("finding_id")
        if not isinstance(finding_id, str) or not finding_id:
            raise FixLocalInvocationError("schema_invalid", "finding_check.finding_id is missing")
        if finding_id not in finding_ids:
            raise FixLocalInvocationError("unknown_finding_id", f"finding_check references unknown finding_id: {finding_id}")
        resolution_method = item.get("resolution_method")
        if resolution_method not in ("rescan", "test", "assertion"):
            raise FixLocalInvocationError("schema_invalid", f"finding_check.resolution_method is invalid: {resolution_method!r}")
        check = item.get("check")
        if not isinstance(check, str) or not check.strip():
            raise FixLocalInvocationError("schema_invalid", "finding_check.check must be concrete and non-empty")
        expected_result = item.get("expected_result")
        if not isinstance(expected_result, str) or not expected_result.strip():
            raise FixLocalInvocationError("schema_invalid", "finding_check.expected_result must be concrete and non-empty")
        checks.append(FindingCheck(finding_id, resolution_method, check, expected_result))

    # Verify every finding is covered
    covered = {c.finding_id for c in checks}
    if not covered >= finding_ids:
        missing = finding_ids - covered
        raise FixLocalInvocationError("verification_contract_incomplete", f"finding_checks do not cover every finding; missing: {missing}")

    # Parse validations
    raw_validations = value.get("validations")
    if not isinstance(raw_validations, list):
        raise FixLocalInvocationError("schema_invalid", "success_definition.validations is not a list")
    validations: list[ValidationSpec] = []
    seen_ids: set[str] = set()
    for item in raw_validations:
        if not isinstance(item, dict):
            raise FixLocalInvocationError("schema_invalid", "validation is not an object")
        vid = item.get("id")
        if not isinstance(vid, str) or not vid.strip():
            raise FixLocalInvocationError("schema_invalid", "validation.id must be a stable non-empty string")
        if vid in seen_ids:
            raise FixLocalInvocationError("schema_invalid", f"validation.id is not unique: {vid}")
        seen_ids.add(vid)
        command_or_assertion = item.get("command_or_assertion")
        if not isinstance(command_or_assertion, str) or not command_or_assertion.strip():
            raise FixLocalInvocationError("schema_invalid", "validation.command_or_assertion must be concrete and non-empty")
        expected = item.get("expected_result")
        if not isinstance(expected, str) or not expected.strip():
            raise FixLocalInvocationError("schema_invalid", "validation.expected_result must be non-empty")
        validations.append(ValidationSpec(vid, command_or_assertion, expected))

    # Parse must_not_regress
    raw_must_not_regress = value.get("must_not_regress")
    if not isinstance(raw_must_not_regress, list):
        raise FixLocalInvocationError("schema_invalid", "success_definition.must_not_regress is not a list")
    must_not_regress = [str(item) for item in raw_must_not_regress]

    # Parse success_rule
    success_rule = value.get("success_rule")
    if not isinstance(success_rule, str) or not success_rule.strip():
        raise FixLocalInvocationError("verification_contract_incomplete", "success_definition.success_rule must be non-empty")

    return SuccessDefinition(checks, validations, must_not_regress, success_rule)


def invoke_local_fix(
    worker: Any,
    plan: ImplementationPlan,
    findings: FindingsList,
    manifest: FixScopedManifest,
    *,
    repository_root: Any = None,
    configured_test_commands: Any = None,
) -> FixIgnitionResult:
    """Invoke the configured local model once for Fix analysis only.

    The single response must provide status, plan, gap_corrections_applied,
    and success_definition.  No second model invocation is performed.

    Before the model call, a ``ValidationSourceCatalog`` is built
    deterministically from repository evidence.  The catalog is included
    in the prompt so the model can select only from known sources.

    After the model returns, a ``ValidationGroundingResolver`` independently
    verifies every check and validation is grounded.  The model is never
    the source of authority.
    """
    from audisor.schemas.task_input import TaskInput
    from pathlib import Path
    from audisor_backend.policies.fix.validation_grounding import (
        ValidationGroundingResolver,
        GroundingError,
        build_validation_source_catalog,
    )

    # Build the validation source catalog before the model call
    root = Path(repository_root) if repository_root else Path.cwd()
    test_cmds = configured_test_commands or ()
    catalog = build_validation_source_catalog(
        repository_root=root,
        findings=findings,
        manifest=manifest,
        plan=plan,
        configured_test_commands=test_cmds,
    )

    worker.structured_output = True
    prompt = {
        "instruction": (
            "Evaluate this scan-identified Fix plan as advisory analysis only. "
            "Return exactly one JSON object with status, plan, gap_corrections_applied, "
            "and success_definition. Do not score findings, choose ready or isolated items, "
            "execute commands, mutate files, approve changes, or produce a final result. "
            "Repair only the listed findings. "
            "The success_definition must include finding_checks covering every finding, "
            "validations with stable IDs and concrete commands or assertions, "
            "must_not_regress constraints, and a non-empty success_rule. "
            "Only include validations supported by the provided validation_source_catalog. "
            "Do not invent new commands, files, checks, or test runners. "
            "Select only from the catalog sources. "
            "Use success_rule = 'all_finding_checks_and_validations_pass'."
        ),
        "plan": {"steps": [step.__dict__ for step in plan.steps], "target_files": plan.target_files},
        "findings": [finding.__dict__ for finding in findings],
        "manifest": manifest.__dict__,
        "validation_source_catalog": catalog.to_mapping(),
    }
    try:
        response = worker.execute(TaskInput(task_id="aflow-fix", prompt=json.dumps(prompt, sort_keys=True)))
    except Exception as exc:
        raise FixLocalInvocationError("provider_failed", "Fix local provider request failed") from exc
    value = _one_json_object(getattr(response, "answer", None))
    if set(value) != {"status", "plan", "gap_corrections_applied", "success_definition"} or value["status"] not in {"accepted", "rejected"}:
        raise FixLocalInvocationError("schema_invalid", "Fix model response fields are invalid")
    corrected = _plan_from_json(value["plan"], plan, findings, manifest)
    if int(value["gap_corrections_applied"]) < 0:
        raise FixLocalInvocationError("decision_inconsistent", "Fix gap correction count is invalid")
    success_definition = _success_definition_from_json(value["success_definition"], findings, manifest)

    # Deterministically verify grounding after the model returns
    resolver = ValidationGroundingResolver()
    try:
        grounding = resolver.resolve(
            repository_root=root,
            findings=findings,
            manifest=manifest,
            plan=corrected,
            success_definition=success_definition,
            catalog=catalog,
        )
    except GroundingError as exc:
        raise FixLocalInvocationError(exc.code, str(exc)) from exc

    return FixIgnitionResult(
        corrected,
        accepted=value["status"] == "accepted",
        success_definition=success_definition,
        verification_grounding=grounding,
    )


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
