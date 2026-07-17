from __future__ import annotations

from datetime import timezone
from typing import Any

from aflow.analysis.decision_engine import Clock, utc_now
from aflow.domain.models import DomainInvariantError, validate_domain_invariants
from aflow.storage.hashing import artifact_ref, seal


def lock_plan(
    request: dict[str, Any], decision: dict[str, Any], *, clock: Clock = utc_now
) -> dict[str, Any]:
    validate_domain_invariants(request["success_definition"], "success_definition")
    validate_domain_invariants(request["plan"], "plan")
    validate_domain_invariants(decision, "analysis_decision")
    if decision["decision"] != "no_material_gap" or not decision["execution_ready"]:
        raise DomainInvariantError("only a no_material_gap, execution-ready plan can be locked")
    expected_plan = artifact_ref(request["plan"], "plan.schema.json", id_field="plan_id")
    expected_success = artifact_ref(
        request["success_definition"], "success-definition.schema.json", id_field="success_definition_id"
    )
    if decision["plan_reference"] != expected_plan or decision["success_definition_reference"] != expected_success:
        raise DomainInvariantError("decision references do not exactly bind the accepted artifacts")
    locked = {
        "schema_version": "1.0.0",
        "lock_id": f"lock.{request['plan']['plan_id']}.{request['plan']['version']}",
        "analysis_decision_reference": artifact_ref(
            decision, "analysis-decision.schema.json", id_field="analysis_id"
        ),
        "plan_reference": expected_plan,
        "success_definition_reference": expected_success,
        "authority_bundle_reference": artifact_ref(
            request["authority_evidence"], "authority-evidence.schema.json", id_field="authority_bundle_id"
        ),
        "baseline_reference": artifact_ref(
            request["baseline"], "baseline.schema.json", id_field="baseline_id"
        ),
        "locked_by": "aflow",
        "locked_at": clock().astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    return validate_domain_invariants(seal(locked, "lock_hash"), "locked_plan")

