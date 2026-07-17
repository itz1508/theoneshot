from __future__ import annotations

from copy import deepcopy

import pytest

from aflow.analysis.decision_engine import analyze
from aflow.drift.classification import classify
from aflow.drift.compare import compare_baselines
from aflow.drift.capture import capture_baseline
from aflow.fixtures.factory import evidence, fixed_clock
from aflow.lifecycle.closure import close_findings
from aflow.lifecycle.locking import lock_plan
from aflow.lifecycle.transitions import LifecycleState, transition
from aflow.storage.hashing import artifact_ref, canonical_hash, seal


def test_lifecycle_rejects_invalid_transition():
    assert transition(LifecycleState.DRAFT, LifecycleState.ANALYZED) == LifecycleState.ANALYZED
    with pytest.raises(ValueError):
        transition(LifecycleState.DRAFT, LifecycleState.LOCKED)


def test_only_clean_exact_plan_can_lock(clean_request):
    clean = analyze(clean_request, clock=fixed_clock)
    locked = lock_plan(clean_request, clean, clock=fixed_clock)
    assert locked["plan_reference"] == clean["plan_reference"]
    blocked_request = deepcopy(clean_request)
    blocked_request["plan"]["actions"][0]["phase"] = "design"
    blocked = analyze(blocked_request, clock=fixed_clock)
    with pytest.raises(ValueError):
        lock_plan(blocked_request, blocked, clock=fixed_clock)


def _closure_case(clean_request, effective: bool):
    original = deepcopy(clean_request)
    original["plan"]["validations"][0]["required_environment"] = "same-process"
    prior = analyze(original, clock=fixed_clock)
    revised = deepcopy(original)
    revised["analysis_id"] = "analysis.revised"
    revised["plan"]["version"] = "1.0.1"
    if effective:
        revised["plan"]["validations"][0]["required_environment"] = "production-host"
    added = evidence("evidence.closure-test", "artifact", "Production-host evidence supplied.", visibility="closure_input")
    revised["evidence"].append(added)
    request = {
        "schema_version": "1.0.0", "closure_id": "closure.test",
        "prior_decision_reference": artifact_ref(prior, "analysis-decision.schema.json", id_field="analysis_id"),
        "original_plan_reference": artifact_ref(original["plan"], "plan.schema.json", id_field="plan_id"),
        "revised_plan": revised["plan"], "added_evidence": [added],
    }
    return close_findings(request, prior_decision=prior, original_plan=original["plan"], revised_analysis_request=revised, clock=fixed_clock)


def test_closure_uses_original_predicate_not_version_bump(clean_request):
    assert _closure_case(clean_request, effective=False)["finding_results"][0]["status"] == "open"
    assert _closure_case(clean_request, effective=True)["finding_results"][0]["status"] == "closed"


def test_closure_rejects_success_definition_weakening(clean_request):
    original = deepcopy(clean_request)
    original["plan"]["validations"][0]["required_environment"] = "same-process"
    prior = analyze(original, clock=fixed_clock)
    revised = deepcopy(original)
    revised["analysis_id"] = "analysis.weakened"
    revised["success_definition"]["requirements"][0]["statement"] = "Produce anything."
    revised["success_definition"]["confirmation"]["content_hash"] = canonical_hash(revised["success_definition"])
    revised["plan"]["success_definition_reference"] = artifact_ref(revised["success_definition"], "success-definition.schema.json", id_field="success_definition_id")
    added = evidence("evidence.weaken", "artifact", "Unrelated evidence.", visibility="closure_input")
    revised["evidence"].append(added)
    request = {
        "schema_version": "1.0.0", "closure_id": "closure.weaken",
        "prior_decision_reference": artifact_ref(prior, "analysis-decision.schema.json", id_field="analysis_id"),
        "original_plan_reference": artifact_ref(original["plan"], "plan.schema.json", id_field="plan_id"),
        "revised_plan": revised["plan"], "added_evidence": [added],
    }
    with pytest.raises(ValueError, match="weakened"):
        close_findings(request, prior_decision=prior, original_plan=original["plan"], revised_analysis_request=revised, clock=fixed_clock)


def test_filesystem_capture_supports_unborn_untracked_without_mutation(tmp_path, monkeypatch):
    target = tmp_path / "src"
    target.mkdir()
    sample = target / "café.txt"
    sample.write_text("東京", encoding="utf-8")
    before = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("git/subprocess invoked")))
    captured = capture_baseline(
        tmp_path, baseline_id="baseline.unborn", relevant_paths=["src"], protected_paths=[],
        authority_hashes=[], repository_kind="git", git_state="unborn", dirty_state="untracked", clock=fixed_clock,
    )
    after = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert captured["git_state"] == "unborn"
    assert captured["dirty_state"] == "untracked"
    assert before == after


@pytest.mark.parametrize(
    ("classification", "expected", "blocking"),
    [("unrelated", "nonblocking_drift_recorded", False), ("relevant", "scoped_revalidation_required", True),
     ("protected", "full_reanalysis_required", True), ("unknown", "baseline_unverifiable", True)],
)
def test_drift_classification(clean_request, classification, expected, blocking):
    locked = clean_request["baseline"]
    if classification == "unrelated":
        locked = deepcopy(locked)
        locked["entries"][0]["classification"] = "unrelated"
        locked = seal(locked)
    current = deepcopy(locked)
    current["baseline_id"] = "baseline.current"
    current["entries"][0]["classification"] = classification
    current["entries"][0]["content_hash"] = canonical_hash("changed")
    current = seal(current)
    result = classify(compare_baselines(locked, current))
    assert (result["decision"], result["blocking"]) == (expected, blocking)


def test_current_baseline_cannot_downgrade_locked_classification_or_scope(clean_request):
    locked = clean_request["baseline"]
    relabeled = deepcopy(locked)
    relabeled["baseline_id"] = "baseline.relabeled"
    relabeled["entries"][0]["classification"] = "unrelated"
    relabeled = seal(relabeled)
    decision = classify(compare_baselines(locked, relabeled))
    assert decision["decision"] == "baseline_unverifiable"
    assert decision["blocking"] is True

    rescoped = deepcopy(locked)
    rescoped["baseline_id"] = "baseline.rescoped"
    rescoped["scope"]["relevant_paths"] = ["elsewhere"]
    rescoped = seal(rescoped)
    decision = classify(compare_baselines(locked, rescoped))
    assert decision["decision"] == "baseline_unverifiable"
