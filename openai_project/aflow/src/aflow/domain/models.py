from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aflow.schemas.validator import validate
from aflow.storage.hashing import canonical_hash, verify_hash


class DomainInvariantError(ValueError):
    pass


SCHEMA_BY_KIND = {
    "analysis_request": "analysis-request.schema.json",
    "analysis_decision": "analysis-decision.schema.json",
    "success_definition": "success-definition.schema.json",
    "plan": "plan.schema.json",
    "closure_request": "closure-request.schema.json",
    "closure_result": "closure-result.schema.json",
    "locked_plan": "locked-plan.schema.json",
    "baseline": "baseline.schema.json",
    "authority_evidence": "authority-evidence.schema.json",
    "repository_evidence": "repository-evidence.schema.json",
    "evidence": "evidence.schema.json",
    "drift_event": "drift-event.schema.json",
    "drift_decision": "drift-decision.schema.json",
    "build_result": "build-result.schema.json",
    "requirement_trace": "requirement-evidence-trace.schema.json",
    "final_evaluation": "final-evaluation.schema.json",
}


def _unique(items: list[dict[str, Any]], field: str, scope: str) -> None:
    values = [item[field] for item in items]
    duplicates = sorted({value for value in values if values.count(value) > 1})
    if duplicates:
        raise DomainInvariantError(f"duplicate {scope} IDs: {', '.join(duplicates)}")


def validate_domain_invariants(value: dict[str, Any], kind: str) -> dict[str, Any]:
    validate(value, SCHEMA_BY_KIND[kind])
    if kind == "success_definition":
        _unique(value["actors"], "actor_id", "actor")
        _unique(value["requirements"], "requirement_id", "requirement")
        _unique(value["prohibited_outcomes"], "outcome_id", "prohibited outcome")
        _unique(value["non_goals"], "non_goal_id", "non-goal")
        _unique(value["proof_obligations"], "proof_id", "proof")
        expected = canonical_hash(value)
        if value["confirmation"]["content_hash"] != expected:
            raise DomainInvariantError("success-definition confirmation hash mismatch")
    elif kind == "plan":
        _unique(value["requirements_coverage"], "requirement_id", "requirement coverage")
        _unique(value["actions"], "action_id", "action")
        _unique(value["validations"], "validation_id", "validation")
        _unique(value["dependencies"], "dependency_id", "dependency")
        _unique(value["assumptions"], "assumption_id", "assumption")
        _unique(value["authority_risks"], "risk_id", "authority risk")
        _unique(value["failure_handling"], "failure_id", "failure")
        _unique(value["stop_conditions"], "stop_id", "stop condition")
        outputs = [output for action in value["actions"] for output in action["expected_outputs"]]
        _unique(outputs, "output_id", "expected output")
    elif kind == "analysis_decision":
        _unique(value["findings"], "finding_id", "finding")
        _unique(value["rejected_findings"], "finding_id", "rejected finding")
        clean = value["decision"] == "no_material_gap"
        if clean != (not value["blocking"] and value["execution_ready"] and not value["findings"]):
            raise DomainInvariantError("analysis decision invariant violated")
        if not clean and (not value["blocking"] or value["execution_ready"] or not value["findings"]):
            raise DomainInvariantError("blocking analysis decision invariant violated")
        if not verify_hash(value):
            raise DomainInvariantError("analysis decision content hash mismatch")
    elif kind == "final_evaluation":
        _unique(value["quality_summary"], "dimension", "quality dimension")
        _unique(value["blocking_failures"], "failure_id", "blocking failure")
        proven = value["decision"] == "proven"
        if proven and (value["blocking"] or value["blocking_failures"] or value["prohibited_outcomes_present"]):
            raise DomainInvariantError("proven final decision invariant violated")
        if value["decision"] in {"unproven", "contradicted", "invalidated_by_drift"} and (
            not value["blocking"] or not value["blocking_failures"]
        ):
            raise DomainInvariantError("blocking final decision invariant violated")
        if proven and any(item["status"] not in {"pass", "not_applicable"} for item in value["quality_summary"]):
            raise DomainInvariantError("proven final decision contains an unproven quality dimension")
        if not verify_hash(value):
            raise DomainInvariantError("final evaluation content hash mismatch")
    elif kind == "locked_plan":
        if not verify_hash(value, "lock_hash"):
            raise DomainInvariantError("locked-plan hash mismatch")
    elif kind == "baseline":
        _unique(value["entries"], "path", "baseline path")
        _unique(value["authority_hashes"], "authority_id", "baseline authority")
        if not verify_hash(value):
            raise DomainInvariantError("baseline content hash mismatch")
    elif kind == "authority_evidence":
        _unique(value["sources"], "authority_id", "authority source")
        _unique(value["capabilities"], "capability_id", "capability")
        if not verify_hash(value):
            raise DomainInvariantError("authority evidence content hash mismatch")
    elif kind == "repository_evidence":
        _unique(value["entries"], "path", "repository evidence path")
        if not verify_hash(value):
            raise DomainInvariantError("repository evidence content hash mismatch")
    elif kind == "evidence":
        if not verify_hash(value):
            raise DomainInvariantError("evidence content hash mismatch")
    elif kind == "build_result":
        _unique(value["outputs"], "output_id", "build output")
        _unique(value["validation_results"], "validation_id", "validation result")
        _unique(value["prohibited_outcome_checks"], "outcome_id", "prohibited outcome check")
        if not verify_hash(value):
            raise DomainInvariantError("build result content hash mismatch")
    elif kind == "requirement_trace":
        _unique(value["entries"], "requirement_id", "trace requirement")
        for entry in value["entries"]:
            _unique(entry["quality_results"], "dimension", f"trace quality for {entry['requirement_id']}")
        if not verify_hash(value):
            raise DomainInvariantError("requirement trace content hash mismatch")
    elif kind == "closure_result":
        _unique(value["finding_results"], "finding_id", "closure finding")
        unresolved = any(item["status"] in {"open", "partially_closed"} for item in value["finding_results"])
        if unresolved and value["new_decision"]["decision"] == "no_material_gap":
            raise DomainInvariantError("closure cannot be clean while an original finding remains unresolved")
        if not verify_hash(value):
            raise DomainInvariantError("closure result content hash mismatch")
    elif "content_hash" in value and not verify_hash(value):
        raise DomainInvariantError(f"{kind} content hash mismatch")
    return value


def load_artifact(path: str | Path, kind: str) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise DomainInvariantError("artifact root must be an object")
    return validate_domain_invariants(value, kind)
