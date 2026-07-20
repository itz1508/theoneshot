"""Strict host boundary for the frozen Audisor analysis decision."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol

from jsonschema import Draft202012Validator, FormatChecker

from audisor.schemas.task_input import TaskInput
from audisor.workers.base import ProviderError
from audisor.workers.local import LocalWorker

from .analysis_package import FrozenAnalysisPackage, _registry, package_sha256
from .artifacts import persist_audisor_stage
from .contract import FROZEN_TO_CONTRACT_READINESS, AudisorLifecycleError
from .adapter import PLAN_SECTIONS


class _Worker(Protocol):
    def execute(self, task: TaskInput) -> Any: ...


class AudisorInvocationError(AudisorLifecycleError):
    """Structured, fail-closed error from the local Audisor model bridge."""

    def __init__(self, message: str, *, code: str, detail: str = "", missing_inputs: list[str] | None = None, gaps: list[dict[str, Any]] | None = None, required_corrections: list[str] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail[:1000]
        self.missing_inputs = list(missing_inputs or [])
        self.gaps = list(gaps or [])
        self.required_corrections = list(required_corrections or [])
        self.retry_prompt: str | None = None


def _frozen_canonical_hash(value: Mapping[str, Any]) -> str:
    """Use the frozen helper, loading the sibling package in source checkouts."""
    try:
        from aflow.storage.hashing import canonical_hash
    except ModuleNotFoundError:
        path = Path(__file__).resolve().parents[4] / "aflow" / "src" / "aflow" / "storage" / "hashing.py"
        spec = importlib.util.spec_from_file_location("_frozen_aflow_hashing", path)
        if spec is None or spec.loader is None:
            raise AudisorInvocationError("frozen Audisor hashing helper is unavailable", code="content_hash_mismatch")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        canonical_hash = module.canonical_hash
    return canonical_hash(dict(value))


def _load_stage(context: Any, name: str) -> dict[str, Any]:
    path = Path(context.workspace_identity["path"]) / "audisor-artifacts" / f"{name}.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AudisorInvocationError("persisted Audisor package is unavailable", code="package_persistence_failed", detail=str(exc)) from exc
    if not isinstance(value, dict):
        raise AudisorInvocationError("persisted Audisor package is malformed", code="package_hash_mismatch")
    return value


def _prompt(persisted_package: Mapping[str, Any], *, decided_at: str) -> str:
    payload = {
        "package_sha256": persisted_package["package_sha256"],
        "package": persisted_package["package"],
    }
    return (
        "Evaluate the supplied sealed Audisor analysis package. Do not generate a new plan "
        "or success definition. Use only evidence contained in the package. Do not scan, "
        "call tools, choose providers, execute commands, create locks, or mutate files. "
        "Return exactly one JSON object, with no Markdown fences, prose, second object, "
        "or tool call. Return only these analytical candidate fields: decision, "
        "blocking, execution_ready, findings, and rejected_findings. Do not "
        "return schema_version, analysis_id, success_definition_reference, "
        "plan_reference, decided_at, or content_hash; the host owns those "
        "fields and reconstructs them exactly. Do not return legacy fields: material_gap_found, "
        "evaluation_permitted, correction_required, unresolved_items, "
        "implementation_eligible, or execution_authorized. "
        "The candidate object must contain exactly the five fields named above. "
        "Each finding must contain exactly "
        "schema_version, finding_id, gap_type, origin, severity, blocking, "
        "requirement_references, plan_locations, specific_claim, evidence_references, "
        "reasoning, why_it_matters, required_closure, and status. Finding gap_type "
        "must use the frozen enum and evidence_references must resolve to package evidence. "
        "The decision table is: no_material_gap means blocking=false, "
        "execution_ready=true, findings=[]; material_gap_found, missing_evidence, "
        "contradicted, and drift_revalidation_required each mean blocking=true, "
        "execution_ready=false, and findings must be non-empty. Findings must conform "
        "to the frozen Audisor Finding schema. Audisor has no mutation, execution, "
        "apply, release, or completion authority. Do not copy plan actions or "
        "any other package object into the response. The top-level decision "
        "value is the scalar string no_material_gap, material_gap_found, "
        "missing_evidence, contradicted, or drift_revalidation_required, not "
        "a nested object. For a clean result, follow this shape exactly: "
        "{decision: \\\"no_material_gap\\\", blocking: false, "
        "execution_ready: true, findings: [], rejected_findings: []}.\n\n"
        + "\n\nThe exact candidate JSON Schema is:\n"
        + json.dumps(_candidate_schema(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n\nThe sealed package is:\n"
        + json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n\nNow output exactly one JSON object with only these five keys and no others: "
        + '{"decision":"no_material_gap","blocking":false,"execution_ready":true,"findings":[],"rejected_findings":[]}'
    )


def _decode_one_object(answer: Any) -> dict[str, Any]:
    if not isinstance(answer, str) or not answer.strip():
        raise AudisorInvocationError("local Audisor output is empty", code="empty_response")
    text = answer.strip()
    if text.startswith("```") or text.endswith("```"):
        raise AudisorInvocationError("Markdown response framing is not allowed", code="invalid_response_framing")
    decoder = json.JSONDecoder()
    try:
        value, end = decoder.raw_decode(text)
    except json.JSONDecodeError as exc:
        raise AudisorInvocationError("local Audisor output is not valid JSON", code="invalid_json", detail=str(exc)) from exc
    if text[end:].strip() or not isinstance(value, dict):
        raise AudisorInvocationError("Audisor response must contain exactly one JSON object", code="invalid_response_framing")
    return value


FORBIDDEN_FIELDS = {
    "material_gap_found",
    "evaluation_permitted",
    "correction_required",
    "unresolved_items",
    "implementation_eligible",
    "execution_authorized",
}
HOST_OWNED_FIELDS = {
    "schema_version",
    "analysis_id",
    "success_definition_reference",
    "plan_reference",
    "decided_at",
    "content_hash",
}
LEGACY_FIELDS = FORBIDDEN_FIELDS
CANDIDATE_FIELDS = {"decision", "blocking", "execution_ready", "findings", "rejected_findings"}


def _candidate_schema() -> dict[str, Any]:
    """Derive the private candidate schema by removing exactly six host fields."""
    documents, _registry_map = _registry()
    schema = copy.deepcopy(documents["analysis-decision.schema.json"])
    original_required = set(schema["required"])
    for field in HOST_OWNED_FIELDS:
        schema["properties"].pop(field, None)
    schema["required"] = sorted(original_required - HOST_OWNED_FIELDS)
    return schema


def _validate_decision_consistency(value: Mapping[str, Any]) -> None:
    clean = value.get("decision") == "no_material_gap"
    if value.get("blocking") != (not clean) or value.get("execution_ready") != clean:
        raise AudisorInvocationError("Audisor decision fields are inconsistent", code="decision_inconsistent")
    if clean and value.get("findings") != []:
        raise AudisorInvocationError("Audisor clean decision contains findings", code="decision_inconsistent")
    if not clean and not value.get("findings"):
        raise AudisorInvocationError("Audisor blocking decision has no findings", code="decision_inconsistent")


def _validate_candidate(value: Mapping[str, Any], package: Mapping[str, Any]) -> dict[str, Any]:
    supplied = set(value)
    if HOST_OWNED_FIELDS & supplied:
        raise AudisorInvocationError("host-owned fields are not model-authored", code="host_owned_field_in_candidate")
    if LEGACY_FIELDS & supplied:
        raise AudisorInvocationError("legacy Audisor fields are forbidden", code="forbidden_legacy_fields")
    if supplied - CANDIDATE_FIELDS:
        raise AudisorInvocationError("Audisor candidate contains unknown fields", code="candidate_schema_failed")
    if supplied != CANDIDATE_FIELDS:
        raise AudisorInvocationError("Audisor candidate fields are incomplete", code="candidate_schema_failed")
    _validate_decision_consistency(value)
    schema = _candidate_schema()
    _documents, registry = _registry()
    errors = sorted(
        Draft202012Validator(schema, registry=registry, format_checker=FormatChecker()).iter_errors(value),
        key=lambda error: (list(error.absolute_path), error.message),
    )
    if errors:
        raise AudisorInvocationError("Audisor candidate schema validation failed", code="candidate_schema_failed", detail=errors[0].message)
    request = package["analysis_request"]
    evidence_ids = {item["evidence_id"] for item in request["evidence"]}
    finding_ids: set[str] = set()
    for finding in value["findings"]:
        finding_ids.add(finding["finding_id"])
        if not set(item["evidence_id"] for item in finding["evidence_references"]) <= evidence_ids:
            raise AudisorInvocationError("Audisor finding has dangling evidence", code="invalid_findings")
    rejected_ids = {item["finding_id"] for item in value["rejected_findings"]}
    if finding_ids & rejected_ids or len(finding_ids) != len(value["findings"]):
        raise AudisorInvocationError("Audisor finding IDs are not unique", code="invalid_findings")
    for rejected in value["rejected_findings"]:
        if not set(item["evidence_id"] for item in rejected["evidence_references"]) <= evidence_ids:
            raise AudisorInvocationError("Audisor rejected finding has dangling evidence", code="invalid_findings")
    return dict(value)


def _host_complete_candidate(candidate: Mapping[str, Any], package: Mapping[str, Any], decided_at: str) -> dict[str, Any]:
    """Reattach host-owned identity, references, and timestamp after candidate validation."""
    request = package["analysis_request"]
    plan = request["plan"]
    return {
        "schema_version": "1.0.0",
        "analysis_id": request["analysis_id"],
        "success_definition_reference": plan["success_definition_reference"],
        "plan_reference": {
            "artifact_id": plan["plan_id"],
            "schema_id": "https://theoneshot.dev/schemas/aflow/v1/plan.schema.json",
            "version": plan["version"],
            "content_hash": _frozen_canonical_hash(plan),
        },
        **dict(candidate),
        "decided_at": decided_at,
    }


def _seal_decision(candidate: Mapping[str, Any]) -> dict[str, Any]:
    decision = dict(candidate)
    decision["content_hash"] = _frozen_canonical_hash(decision)
    documents, registry = _registry()
    errors = list(Draft202012Validator(documents["analysis-decision.schema.json"], registry=registry, format_checker=FormatChecker()).iter_errors(decision))
    if errors:
        raise AudisorInvocationError("sealed Audisor decision schema validation failed", code="final_schema_failed", detail=errors[0].message)
    _validate_decision_consistency(decision)
    if decision["content_hash"] != _frozen_canonical_hash(decision):
        raise AudisorInvocationError("Audisor decision content hash mismatch", code="content_hash_mismatch")
    return decision


def _adapter(decision: Mapping[str, Any], task: Mapping[str, Any], candidate_plan: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
    active = decision["findings"]
    unresolved = [f"{item['finding_id']}: {item['specific_claim']}" for item in active]
    frozen = {
        "decision": decision["decision"],
        "unresolved_items": unresolved,
        "material_gap_found": decision["decision"] == "material_gap_found",
        "evaluation_permitted": decision["execution_ready"],
        "correction_required": decision["blocking"],
    }
    plan = dict(candidate_plan)
    if not all(plan.get(section) is not None for section in PLAN_SECTIONS):
        plan = _build_contract_plan(candidate_plan, context)
    authority = dict(plan.get("authority") or context.get("authority") or {})
    return {
        "frozen_aflow_result": frozen,
        "accepted_task_input": dict(task),
        "authority": authority,
        "baseline_evidence": context["baseline_evidence"],
        "accepted_constraints": context["accepted_constraints"],
        "required_outputs": context["required_outputs"],
        "candidate_implementation_plan": plan,
    }


def _retry_prompt(task: Mapping[str, Any], original_plan: Mapping[str, Any], error: AudisorInvocationError) -> str:
    """Create a complete, non-authoritative correction request for the planner."""
    payload = json.dumps({"task": dict(task), "original_plan": dict(original_plan)}, ensure_ascii=False, sort_keys=True, indent=2)
    gaps = json.dumps(error.gaps, ensure_ascii=False, sort_keys=True, indent=2)
    missing = json.dumps(error.missing_inputs, ensure_ascii=False, sort_keys=True)
    corrections = json.dumps(error.required_corrections, ensure_ascii=False, sort_keys=True)
    return (
        "Revise the original plan and return it for a new Audisor evaluation. "
        "Do not implement files, execute commands, or claim completion.\n\n"
        "Original task and plan:\n" + payload + "\n\n"
        "Audisor failure code: " + error.code + "\n"
        "Reason: " + str(error) + "\n"
        "Missing inputs: " + missing + "\n"
        "Detected gaps: " + gaps + "\n"
        "Required corrections: " + corrections + "\n\n"
        "Return a revised original plan containing exact outputs, observable "
        "success predicates, executable validation commands, expected results, "
        "and fixture cases. If any item remains unknowable, state it explicitly."
    )


def _build_contract_plan(candidate_plan: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
    """Project the accepted Build plan into the existing contract shape.

    This is host-owned normalization: it uses only the sealed Build tasks and
    authority context.  It does not accept analytical fields from the model
    and it does not grant any execution or mutation authority.
    """
    tasks = context.get("build_tasks") or []
    if not isinstance(tasks, list) or not tasks:
        raise AudisorInvocationError("accepted Build plan cannot produce a contract", code="final_schema_failed", detail="build tasks are missing")
    requirements = []
    actions = []
    stages = []
    validations = []
    fixtures = []
    evidence_items = []
    acceptance_rules = []
    for index, task in enumerate(tasks, start=1):
        task_id = str(task["task_id"])
        requirement_id = f"requirement.{task_id}"
        action_id = f"action.{task_id}"
        validation_id = f"validation.{task_id}"
        fixture_id = f"fixture.{task_id}"
        evidence_id = f"evidence.{task_id}"
        checkpoint_id = f"checkpoint.{task_id}"
        outputs = list(task.get("expected_outputs") or [])
        requirements.append({
            "requirement_id": requirement_id,
            "success_predicate": f"Task {task_id} produces all approved expected outputs.",
            "source_reference": f"prepared-build:{context.get('accepted_constraints', {}).get('build_id', 'unknown')}:{task_id}",
        })
        actions.append({
            "action_id": action_id,
            "objective": task.get("title", task_id),
            "target_paths": outputs,
            "requirement_ids": [requirement_id],
        })
        stages.append({
            "stage_id": f"stage.{task_id}",
            "exact_actions": [action_id],
            "checkpoint": {"checkpoint_id": checkpoint_id},
        })
        validations.append({
            "validation_id": validation_id,
            "requirement_ids": [requirement_id],
            "fixture_id": fixture_id,
        })
        fixtures.append({"fixture_id": fixture_id, "validation_ids": [validation_id]})
        evidence_items.append({
            "evidence_id": evidence_id,
            "requirement_ids": [requirement_id],
            "validation_ids": [validation_id],
            "checkpoint_ids": [checkpoint_id],
        })
        acceptance_rules.append({
            "rule_id": f"rule.{task_id}",
            "requirement_ids": [requirement_id],
            "evidence_ids": [evidence_id],
            "final_decision_rule": f"{task_id} validation and output evidence must be present.",
        })
    authority = dict(context.get("authority") or {})
    allowed = authority.get("allowed_paths") or context.get("required_outputs") or []
    prohibited = authority.get("prohibited_paths") or []
    authority.update({
        "allowed_paths": list(allowed),
        "prohibited_paths": list(prohibited),
        "allowed_tools": list(authority.get("allowed_tools") or []),
        "prohibited_tools": list(authority.get("prohibited_tools") or []),
        "preserved_conditions": list(authority.get("preserved_conditions") or []),
    })
    preserved = authority["preserved_conditions"]
    for condition in preserved:
        condition_id = condition.get("condition_id") if isinstance(condition, Mapping) else condition
        if isinstance(condition_id, str):
            evidence_items.append({"evidence_id": f"evidence.preserved.{condition_id}", "checkpoint_ids": [item["checkpoint_id"] for item in [stage["checkpoint"] for stage in stages]]})
    return {
        "success_definition": {
            "requirements": requirements,
            "source": "sealed prepared Build",
        },
        "execution_trajectory": stages,
        "implementation_plan": actions,
        "validation_contract": validations,
        "fixture_specifications": fixtures,
        "evidence_manifest": {"evidence_items": evidence_items, "state_checks": []},
        "post_build_acceptance": {"acceptance_rules": acceptance_rules},
        "authority": authority,
    }


def invoke_audisor_analysis(
    task: Mapping[str, Any],
    candidate: Mapping[str, Any],
    repository_context: Mapping[str, Any],
    *,
    worker: _Worker | None = None,
    operation_context: Any | None = None,
    package: FrozenAnalysisPackage | None = None,
    decided_at: str | None = None,
) -> Mapping[str, Any]:
    """Call the configured local model exactly once and return adapter input."""
    if package is None or operation_context is None:
        raise AudisorInvocationError("sealed Audisor package is required", code="package_persistence_failed")
    persisted = _load_stage(operation_context, "analysis-package")
    if persisted.get("package_sha256") != package_sha256(persisted.get("package", {})) or persisted.get("package_sha256") != package.package_hash:
        raise AudisorInvocationError("sealed Audisor package hash mismatch", code="package_hash_mismatch")
    decided_at = decided_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    local_worker = worker or LocalWorker.from_environment()
    if isinstance(local_worker, LocalWorker):
        local_worker.structured_output = True
        # Bound the analytical record so the model cannot spend the request
        # budget reproducing plan artifacts or other package contents.
        local_worker.max_tokens = min(local_worker.max_tokens, 160)
    request = TaskInput(task_id=str(task.get("id", "aflow-preflight")), prompt=_prompt(persisted, decided_at=decided_at))
    try:
        response = local_worker.execute(request)
    except ProviderError as exc:
        code = "provider_timeout" if exc.code == "provider_timeout" else "provider_failed"
        failure = AudisorInvocationError("local Audisor provider request failed", code=code, detail=exc.internal_detail)
        failure.retry_prompt = _retry_prompt(task, candidate, failure)
        raise failure from exc
    except Exception as exc:
        failure = AudisorInvocationError("local Audisor provider request failed", code="provider_failed", detail=type(exc).__name__)
        failure.retry_prompt = _retry_prompt(task, candidate, failure)
        raise failure from exc
    answer = getattr(response, "answer", None)
    raw_hash = hashlib.sha256(answer.encode("utf-8") if isinstance(answer, str) else b"").hexdigest()
    persist_audisor_stage(operation_context, "raw-response", {"content": answer, "raw_response_sha256": raw_hash, "http_status": getattr(response, "http_status", None), "finish_reason": getattr(response, "finish_reason", None), "tool_call_present": getattr(response, "tool_call_present", None), "choice_count": getattr(response, "choice_count", None)})
    if getattr(response, "tool_call_present", False):
        raise AudisorInvocationError("Audisor tool calls are not allowed", code="tool_call_not_allowed")
    if getattr(response, "choice_count", None) not in (None, 1):
        raise AudisorInvocationError("Audisor response contains multiple choices", code="invalid_response_framing")
    if getattr(response, "finish_reason", None) not in (None, "stop"):
        raise AudisorInvocationError("Audisor response finish reason is unsupported", code="invalid_response_framing")
    try:
        value = _decode_one_object(answer)
        candidate_value = _validate_candidate(value, persisted["package"])
    except AudisorInvocationError as exc:
        exc.retry_prompt = _retry_prompt(task, candidate, exc)
        raise
    persist_audisor_stage(operation_context, "candidate-analysis", candidate_value)
    sealed = _seal_decision(_host_complete_candidate(candidate_value, persisted["package"], decided_at))
    persist_audisor_stage(operation_context, "sealed-decision", sealed)
    adapter = _adapter(sealed, task, candidate, repository_context)
    persist_audisor_stage(operation_context, "adapter", adapter)
    return adapter
