"""External, fail-closed locked execution-contract adapter.

The frozen ``openai_project/aflow`` package supplies the Audisor decision.  This
module never changes that decision or writes into the frozen tree; it turns a
primary-accepted plan into a deterministic, independently verifiable contract.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import PurePosixPath
from typing import Any, Mapping

from .contract import AudisorLifecycleError, FROZEN_TO_CONTRACT_READINESS, canonical_text, normalize_frozen_readiness

ASSEMBLY_INPUTS = (
    "frozen_aflow_result", "accepted_task_input", "candidate_implementation_plan",
    "authority", "baseline_evidence", "accepted_constraints", "required_outputs",
)
PLAN_SECTIONS = (
    "success_definition", "execution_trajectory", "implementation_plan",
    "validation_contract", "fixture_specifications", "evidence_manifest",
    "post_build_acceptance",
)
# These collections describe sets.  Ordered actions and stages are deliberately
# absent: their author-supplied sequence is part of the execution semantics.
UNORDERED_ID_COLLECTIONS = {"requirements", "validation_contract", "fixture_specifications", "evidence_items", "acceptance_rules"}


def _error(message: str) -> None:
    raise AudisorLifecycleError(message)


def _id_set(rows: Any, key: str, section: str) -> set[str]:
    if not isinstance(rows, list) or not rows:
        _error(f"{section} must be a non-empty list")
    values = [row.get(key) if isinstance(row, Mapping) else None for row in rows]
    if any(not isinstance(value, str) or not value for value in values):
        _error(f"{section} contains a missing {key}")
    if len(values) != len(set(values)):
        _error(f"{section} contains a duplicate {key}")
    return set(values)


def _refs(value: Any, known: set[str], label: str, *, required: bool = True) -> set[str]:
    if not isinstance(value, list) or (required and not value) or any(not isinstance(item, str) for item in value):
        _error(f"{label} must be a {'non-empty ' if required else ''}list of IDs")
    result = set(value)
    if not result <= known:
        _error(f"{label} contains an unresolved reference")
    return result


def _inside(child: str, parent: str) -> bool:
    try:
        candidate, root = PurePosixPath(child), PurePosixPath(parent)
    except TypeError:
        return False
    return candidate == root or root in candidate.parents


def _canonicalize(value: Any, parent_key: str | None = None) -> Any:
    if isinstance(value, Mapping):
        return {key: _canonicalize(value[key], key) for key in sorted(value)}
    if isinstance(value, list):
        normalized = [_canonicalize(item) for item in value]
        if parent_key in UNORDERED_ID_COLLECTIONS:
            id_key = next((key for key in ("requirement_id", "validation_id", "fixture_id", "evidence_id", "rule_id") if all(isinstance(item, Mapping) and key in item for item in normalized)), None)
            if id_key:
                return sorted(normalized, key=lambda item: item[id_key])
        return normalized
    return value


def _body(contract: Mapping[str, Any]) -> dict[str, Any]:
    body = copy.deepcopy(dict(contract))
    lock = body.get("lock_payload")
    if not isinstance(lock, dict):
        _error("missing lock_payload")
    lock.pop("canonical_text", None)
    lock.pop("sha256", None)
    return body


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def verify_contract(contract: Mapping[str, Any]) -> bool:
    """Return false for malformed, tampered, or non-canonical contracts."""
    try:
        body = _body(contract)
        lock = contract["lock_payload"]
        if lock.get("hash_algorithm") != "sha256":
            return False
        normalized = _canonicalize(body)
        text = canonical_text(normalized)
        return (
            body == normalized
            and lock.get("canonical_text") == text
            and lock.get("sha256") == _digest(text)
            and json.loads(text) == body
        )
    except (AudisorLifecycleError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False


def _validate_authority(contract: Mapping[str, Any]) -> None:
    authority = contract["authority"]
    if not isinstance(authority, Mapping):
        _error("authority is malformed")
    allowed, prohibited = authority.get("allowed_paths"), authority.get("prohibited_paths")
    if not isinstance(allowed, list) or not allowed or not isinstance(prohibited, list):
        _error("authority paths are malformed")
    for action in contract["implementation_plan"]:
        paths = action.get("target_paths")
        if not isinstance(paths, list) or not paths:
            _error("action target_paths are missing")
        for path in paths:
            if not isinstance(path, str) or not any(_inside(path, root) for root in allowed):
                _error("action path is outside allowed authority")
            if any(_inside(path, root) for root in prohibited):
                _error("action path conflicts with prohibited authority")
    allowed_tools = set(authority.get("allowed_tools", []))
    prohibited_tools = set(authority.get("prohibited_tools", []))
    if allowed_tools & prohibited_tools:
        _error("authority tool lists conflict")


def _validate_traceability(contract: Mapping[str, Any]) -> None:
    success = contract["success_definition"]
    if not isinstance(success, Mapping):
        _error("success_definition is malformed")
    requirements = success.get("requirements")
    requirement_ids = _id_set(requirements, "requirement_id", "success_definition.requirements")
    for item in requirements:
        for field in ("success_predicate", "source_reference"):
            if not isinstance(item.get(field), str) or not item[field]:
                _error(f"requirement {field} is missing")

    actions = contract["implementation_plan"]
    action_ids = _id_set(actions, "action_id", "implementation_plan")
    covered_by_actions: set[str] = set()
    for action in actions:
        covered_by_actions |= _refs(action.get("requirement_ids"), requirement_ids, "action requirement_ids")
    if covered_by_actions != requirement_ids:
        _error("missing requirement-to-action traceability")

    stages = contract["execution_trajectory"]
    stage_ids = _id_set(stages, "stage_id", "execution_trajectory")
    stage_actions: set[str] = set()
    checkpoint_ids: set[str] = set()
    for stage in stages:
        stage_actions |= _refs(stage.get("exact_actions"), action_ids, "stage exact_actions")
        checkpoint = stage.get("checkpoint")
        if not isinstance(checkpoint, Mapping) or not isinstance(checkpoint.get("checkpoint_id"), str):
            _error("trajectory checkpoint is missing")
        checkpoint_ids.add(checkpoint["checkpoint_id"])
    if len(checkpoint_ids) != len(stages):
        _error("execution_trajectory contains a duplicate checkpoint_id")
    if stage_actions != action_ids:
        _error("action is absent from trajectory")

    validations = contract["validation_contract"]
    validation_ids = _id_set(validations, "validation_id", "validation_contract")
    validation_requirements: set[str] = set()
    validation_fixtures: set[str] = set()
    for validation in validations:
        validation_requirements |= _refs(validation.get("requirement_ids"), requirement_ids, "validation requirement_ids")
        fixture_id = validation.get("fixture_id")
        if not isinstance(fixture_id, str):
            _error("validation fixture_id is missing")
        validation_fixtures.add(fixture_id)
    if validation_requirements != requirement_ids:
        _error("missing requirement-to-validation traceability")

    fixtures = contract["fixture_specifications"]
    fixture_ids = _id_set(fixtures, "fixture_id", "fixture_specifications")
    if not validation_fixtures <= fixture_ids:
        _error("validation fixture reference is invalid")
    fixture_validations: set[str] = set()
    for fixture in fixtures:
        fixture_validations |= _refs(fixture.get("validation_ids"), validation_ids, "fixture validation_ids")
    if fixture_validations != validation_ids:
        _error("missing validation-to-fixture traceability")

    manifest = contract["evidence_manifest"]
    if not isinstance(manifest, Mapping):
        _error("evidence_manifest is malformed")
    evidence = manifest.get("evidence_items")
    evidence_ids = _id_set(evidence, "evidence_id", "evidence_manifest.evidence_items")
    evidence_requirements: set[str] = set()
    evidence_checkpoints: set[str] = set()
    for item in evidence:
        refs_found = False
        for field, known, collector in (
            ("requirement_ids", requirement_ids, evidence_requirements),
            ("validation_ids", validation_ids, None),
            ("checkpoint_ids", checkpoint_ids, evidence_checkpoints),
        ):
            if field in item:
                result = _refs(item[field], known, f"evidence {field}", required=False)
                if result:
                    refs_found = True
                    if collector is not None:
                        collector |= result
        if not refs_found:
            _error("evidence item has no traceability reference")
    if evidence_requirements != requirement_ids:
        _error("every requirement needs evidence")
    if evidence_checkpoints != checkpoint_ids:
        _error("every trajectory checkpoint needs evidence")

    acceptance = contract["post_build_acceptance"]
    rules = acceptance.get("acceptance_rules") if isinstance(acceptance, Mapping) else None
    _id_set(rules, "rule_id", "post_build_acceptance.acceptance_rules")
    accepted_requirements: set[str] = set()
    for rule in rules:
        accepted_requirements |= _refs(rule.get("requirement_ids"), requirement_ids, "acceptance rule requirement_ids")
        _refs(rule.get("evidence_ids"), evidence_ids, "acceptance rule evidence_ids")
        if not isinstance(rule.get("final_decision_rule"), str) or not rule["final_decision_rule"]:
            _error("acceptance rule final_decision_rule is missing")
    if accepted_requirements != requirement_ids:
        _error("every requirement needs a final acceptance rule")

    preserved = contract["authority"].get("preserved_conditions", [])
    state_checks = manifest.get("state_checks", [])
    for condition in preserved:
        condition_id = condition.get("condition_id") if isinstance(condition, Mapping) else condition
        if not isinstance(condition_id, str) or not condition_id:
            _error("preserved condition is malformed")
        if not any(isinstance(check, Mapping) and check.get("condition_id") == condition_id and check.get("evidence_id") in evidence_ids for check in state_checks):
            _error("preserved condition lacks validation or state-check evidence")

    phase_order = contract["authority"].get("phase_order")
    if phase_order is not None and phase_order != [stage["stage_id"] for stage in stages]:
        _error("authority phase_order does not match trajectory order")
    _ = stage_ids  # Ensures the stage IDs are checked even when phase_order is absent.


def _validate(contract: Mapping[str, Any]) -> None:
    for section in PLAN_SECTIONS:
        if contract.get(section) is None:
            _error(f"missing required contract content: {section}")
    _validate_authority(contract)
    _validate_traceability(contract)


def assemble_contract(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate supplied content and return a locked external contract.

    No plan content is fabricated.  A non-ready frozen result still assembles
    a valid contract, but its execution gate remains false.
    """
    for key in ASSEMBLY_INPUTS:
        if key not in value:
            _error(f"missing contract assembly input: {key}")
    frozen = value["frozen_aflow_result"]
    if not isinstance(frozen, Mapping) or frozen.get("decision") not in FROZEN_TO_CONTRACT_READINESS:
        _error("unknown frozen Audisor decision")
    plan = value["candidate_implementation_plan"]
    if not isinstance(plan, Mapping):
        _error("candidate implementation plan is malformed")
    contract: dict[str, Any] = {
        "contract_version": "1.0.0",
        "accepted_task_input": copy.deepcopy(dict(value["accepted_task_input"])),
        "authority": copy.deepcopy(dict(value["authority"])),
        **{section: copy.deepcopy(plan.get(section)) for section in PLAN_SECTIONS},
    }
    contract["accepted_task_input"].update({
        "baseline_evidence": copy.deepcopy(value["baseline_evidence"]),
        "accepted_constraints": copy.deepcopy(value["accepted_constraints"]),
        "required_outputs": copy.deepcopy(value["required_outputs"]),
    })
    _validate(contract)
    readiness = normalize_frozen_readiness(frozen["decision"])
    unresolved = copy.deepcopy(frozen.get("unresolved_items", []))
    ready = readiness["aflow_decision"] == "no_material_gap" and not unresolved
    contract["readiness"] = {
        **readiness,
        "decision_mapping": dict(FROZEN_TO_CONTRACT_READINESS),
        "unresolved_items": unresolved,
        "execution_permitted_when": {
            "aflow_decision_is_no_material_gap": readiness["aflow_decision"] == "no_material_gap",
            "contract_decision_is_no_material_gap": readiness["contract_decision"] == "no_material_gap",
            "unresolved_items_empty": not unresolved,
            "schema_valid": True, "references_valid": True, "traceability_valid": True,
            "authority_valid": True, "canonicalization_valid": True, "lock_valid": True,
            "drift_absent": ready,
        },
    }
    contract["lock_payload"] = {"hash_algorithm": "sha256"}
    contract = _canonicalize(contract)
    text = canonical_text(_body(contract))
    contract["lock_payload"].update({"canonical_text": text, "sha256": _digest(text)})
    if not verify_contract(contract):
        _error("contract lock verification failed")
    return {"aflow_execution_contract": contract}
