from __future__ import annotations

from copy import deepcopy

import pytest

from aflow.analysis.decision_engine import analyze
from aflow.domain.models import DomainInvariantError, validate_domain_invariants
from aflow.evaluation.final_decision import evaluate_result
from aflow.fixtures.factory import build_result, final_evidence, fixed_clock
from aflow.lifecycle.locking import lock_plan
from aflow.storage.hashing import seal
from aflow.storage.hashing import artifact_ref


def _evaluate(clean_request, **build_kwargs):
    decision = analyze(clean_request, clock=fixed_clock)
    locked = lock_plan(clean_request, decision, clock=fixed_clock)
    evidence = build_kwargs.pop("evidence", final_evidence())
    build = build_result(locked, clean_request["baseline"], evidence, **build_kwargs)
    return evaluate_result(
        locked_plan=locked, locked_baseline=clean_request["baseline"], post_build_baseline=clean_request["baseline"],
        success_definition=clean_request["success_definition"], plan=clean_request["plan"],
        build_result=build, evidence=evidence, clock=fixed_clock,
    )


def test_every_quality_dimension_is_reported_and_proven(clean_request):
    trace, final = _evaluate(clean_request)
    assert final["decision"] == "proven"
    assert len(trace["entries"][0]["quality_results"]) == 7
    assert {item["status"] for item in trace["entries"][0]["quality_results"]} == {"pass"}


def test_passing_test_in_substitute_environment_is_unproven(clean_request):
    _, final = _evaluate(clean_request, environment_matches=False)
    assert final["decision"] == "unproven"
    assert final["blocking"] is True


def test_failed_output_quality_contradicts(clean_request):
    ev = final_evidence(quality={"usability": "fail"})
    trace, final = _evaluate(clean_request, evidence=ev)
    assert trace["entries"][0]["status"] == "contradicted"
    assert final["decision"] == "contradicted"


def test_prohibited_outcome_cannot_be_overridden_by_pass_metadata(clean_request):
    trace, final = _evaluate(clean_request, prohibited=True)
    assert trace["entries"][0]["status"] == "contradicted"
    assert final["prohibited_outcomes_present"] == ["outcome.data-loss"]


def test_builder_asserted_quality_and_missing_prohibited_check_are_unproven(clean_request):
    evidence = final_evidence()
    for item in evidence:
        item["collected_by"] = "codex"
        item.update(seal(item))
    decision = analyze(clean_request, clock=fixed_clock)
    locked = lock_plan(clean_request, decision, clock=fixed_clock)
    build = build_result(locked, clean_request["baseline"], evidence)
    build["prohibited_outcome_checks"] = []
    build = seal(build)
    trace, final = evaluate_result(
        locked_plan=locked, locked_baseline=clean_request["baseline"], post_build_baseline=clean_request["baseline"],
        success_definition=clean_request["success_definition"], plan=clean_request["plan"],
        build_result=build, evidence=evidence, clock=fixed_clock,
    )
    assert trace["entries"][0]["status"] == "unproven"
    assert final["decision"] == "unproven"


def test_analysis_or_closure_evidence_cannot_prove_returned_build(clean_request):
    evidence = final_evidence()
    for item in evidence:
        item["visibility"] = "analysis_input"
        item.update(seal(item))
    decision = analyze(clean_request, clock=fixed_clock)
    locked = lock_plan(clean_request, decision, clock=fixed_clock)
    build = build_result(locked, clean_request["baseline"], evidence)
    trace, final = evaluate_result(
        locked_plan=locked, locked_baseline=clean_request["baseline"], post_build_baseline=clean_request["baseline"],
        success_definition=clean_request["success_definition"], plan=clean_request["plan"],
        build_result=build, evidence=evidence, clock=fixed_clock,
    )
    assert trace["entries"][0]["status"] == "unproven"
    assert final["decision"] == "unproven"


def test_corrupted_final_decision_is_rejected(clean_request):
    _, final = _evaluate(clean_request)
    corrupt = deepcopy(final)
    corrupt["decision"] = "unproven"
    corrupt["blocking"] = False
    corrupt = seal(corrupt)
    with pytest.raises((DomainInvariantError, ValueError)):
        validate_domain_invariants(corrupt, "final_evaluation")


def test_missing_actual_output_is_unproven(clean_request):
    decision = analyze(clean_request, clock=fixed_clock)
    locked = lock_plan(clean_request, decision, clock=fixed_clock)
    evidence = final_evidence()
    build = build_result(locked, clean_request["baseline"], evidence)
    build["outputs"] = []
    build = seal(build)
    trace, final = evaluate_result(
        locked_plan=locked, locked_baseline=clean_request["baseline"], post_build_baseline=clean_request["baseline"],
        success_definition=clean_request["success_definition"], plan=clean_request["plan"],
        build_result=build, evidence=evidence, clock=fixed_clock,
    )
    assert trace["entries"][0]["status"] == "unproven"
    assert final["decision"] == "unproven"


def test_unknown_requirement_and_changed_baseline_without_drift_are_rejected(clean_request):
    decision = analyze(clean_request, clock=fixed_clock)
    locked = lock_plan(clean_request, decision, clock=fixed_clock)
    evidence = final_evidence()
    build = build_result(locked, clean_request["baseline"], evidence)
    build["outputs"][0]["requirement_ids"] = ["requirement.unknown"]
    build = seal(build)
    with pytest.raises(ValueError, match="outside the locked"):
        evaluate_result(locked_plan=locked, locked_baseline=clean_request["baseline"], post_build_baseline=clean_request["baseline"], success_definition=clean_request["success_definition"], plan=clean_request["plan"], build_result=build, evidence=evidence)

    changed = deepcopy(clean_request["baseline"])
    changed["baseline_id"] = "baseline.changed"
    changed["entries"][0]["content_hash"] = "sha256:" + "0" * 64
    changed = seal(changed)
    build = build_result(locked, changed, evidence)
    trace, final = evaluate_result(
        locked_plan=locked, locked_baseline=clean_request["baseline"], post_build_baseline=changed,
        success_definition=clean_request["success_definition"], plan=clean_request["plan"], build_result=build, evidence=evidence,
    )
    assert trace["entries"][0]["status"] == "invalidated_by_drift"
    assert final["decision"] == "invalidated_by_drift"


def test_relevant_drift_invalidates_requirement_status(clean_request):
    decision = analyze(clean_request, clock=fixed_clock)
    locked = lock_plan(clean_request, decision, clock=fixed_clock)
    evidence = final_evidence()
    changed = deepcopy(clean_request["baseline"])
    changed["baseline_id"] = "baseline.post-build"
    changed["entries"][0]["content_hash"] = "sha256:" + "1" * 64
    changed = seal(changed)
    build = build_result(locked, changed, evidence)
    trace, final = evaluate_result(
        locked_plan=locked, locked_baseline=clean_request["baseline"], post_build_baseline=changed,
        success_definition=clean_request["success_definition"], plan=clean_request["plan"],
        build_result=build, evidence=evidence, clock=fixed_clock,
    )
    assert trace["entries"][0]["status"] == "invalidated_by_drift"
    assert final["decision"] == "invalidated_by_drift"
