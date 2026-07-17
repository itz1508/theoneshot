from __future__ import annotations

from copy import deepcopy

from aflow.analysis.decision_engine import analyze
from aflow.evaluation.final_decision import evaluate_result
from aflow.fixtures.factory import build_result, evidence, final_evidence, fixed_clock
from aflow.lifecycle.closure import close_findings
from aflow.lifecycle.locking import lock_plan
from aflow.storage.hashing import artifact_ref, seal


def test_end_to_end_gap_closure_lock_and_output_evaluation(clean_request):
    original = deepcopy(clean_request)
    original["plan"]["validations"][0]["required_environment"] = "mock-runtime"
    original["evidence"][0]["claim"] = "A mocked runtime passed; the production host was not exercised."
    original["evidence"][0] = seal(original["evidence"][0])
    blocked = analyze(original, clock=fixed_clock)
    assert blocked["decision"] == "missing_evidence"

    revised = deepcopy(original)
    revised["analysis_id"] = "analysis.integration-revised"
    revised["plan"]["version"] = "1.0.1"
    revised["plan"]["validations"][0]["required_environment"] = "production-host"
    added = evidence("evidence.integration-closure", "artifact", "Production-host restart proof supplied.", visibility="closure_input")
    revised["evidence"].append(added)
    closure_request = {
        "schema_version": "1.0.0", "closure_id": "closure.integration",
        "prior_decision_reference": artifact_ref(blocked, "analysis-decision.schema.json", id_field="analysis_id"),
        "original_plan_reference": artifact_ref(original["plan"], "plan.schema.json", id_field="plan_id"),
        "revised_plan": revised["plan"], "added_evidence": [added],
    }
    closure = close_findings(
        closure_request, prior_decision=blocked, original_plan=original["plan"],
        revised_analysis_request=revised, clock=fixed_clock,
    )
    assert closure["finding_results"][0]["status"] == "closed"
    assert closure["new_decision"]["decision"] == "no_material_gap"

    locked = lock_plan(revised, closure["new_decision"], clock=fixed_clock)
    weak_evidence = final_evidence()
    weak_build = build_result(locked, revised["baseline"], weak_evidence, environment_matches=False)
    _, weak = evaluate_result(
        locked_plan=locked, locked_baseline=revised["baseline"], post_build_baseline=revised["baseline"],
        success_definition=revised["success_definition"], plan=revised["plan"],
        build_result=weak_build, evidence=weak_evidence, clock=fixed_clock,
    )
    assert weak["decision"] == "unproven"

    strong_evidence = final_evidence()
    strong_build = build_result(locked, revised["baseline"], strong_evidence)
    _, strong = evaluate_result(
        locked_plan=locked, locked_baseline=revised["baseline"], post_build_baseline=revised["baseline"],
        success_definition=revised["success_definition"], plan=revised["plan"],
        build_result=strong_build, evidence=strong_evidence, clock=fixed_clock,
    )
    assert strong["decision"] == "proven"
