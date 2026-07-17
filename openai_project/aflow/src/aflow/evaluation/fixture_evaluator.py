from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from aflow.adapters.semantic_reviewer import StaticSemanticReviewer
from aflow.analysis.decision_engine import analyze
from aflow.domain.models import validate_domain_invariants
from aflow.drift.classification import classify
from aflow.drift.compare import compare_baselines
from aflow.lifecycle.closure import close_findings
from aflow.schemas.validator import validate

from .final_decision import evaluate_result


METRICS = (
    "schema_admission_accuracy", "true_gap_detection", "false_gap_rejection",
    "finding_substantiation", "decision_consistency", "closure_accuracy",
    "output_quality_evaluation_accuracy",
)


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _analysis_request(inputs: Path) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0", "analysis_id": "analysis.fixture",
        "success_definition": _load(inputs / "success-definition.json"),
        "plan": _load(inputs / "plan.json"),
        "authority_evidence": _load(inputs / "authority-evidence.json"),
        "repository_evidence": _load(inputs / "repository-evidence.json"),
        "baseline": _load(inputs / "baseline.json"),
        "evidence": _load(inputs / "evidence.json"),
    }


def _analysis_predicates(actual: dict[str, Any], expected: dict[str, Any]) -> list[str]:
    failures = []
    for field in ("decision", "blocking", "execution_ready"):
        if actual[field] != expected[field]:
            failures.append(f"{field}: expected {expected[field]!r}, got {actual[field]!r}")
    count = len(actual["findings"])
    if not expected["finding_count"]["min"] <= count <= expected["finding_count"]["max"]:
        failures.append(f"finding_count: {count} outside expected bounds")
    gaps = {item["gap_type"] for item in actual["findings"]}
    if not set(expected["required_gap_types"]).issubset(gaps):
        failures.append(f"required gap types missing: {set(expected['required_gap_types']) - gaps}")
    requirements = {req for item in actual["findings"] for req in item["requirement_references"]}
    if not set(expected.get("required_requirement_references", [])).issubset(requirements):
        failures.append("required requirement references missing")
    locations = {location for item in actual["findings"] for location in item["plan_locations"]}
    if not set(expected.get("required_plan_locations", [])).issubset(locations):
        failures.append(f"required plan locations missing: {set(expected.get('required_plan_locations', [])) - locations}")
    closures = {item["required_closure"]["closure_code"] for item in actual["findings"]}
    if not set(expected.get("required_closure_codes", [])).issubset(closures):
        failures.append("required closure codes missing")
    if len(actual["rejected_findings"]) < expected.get("minimum_rejected_findings", 0):
        failures.append("too few rejected false findings")
    if expected["forbid_unexpected_findings"] and actual["findings"]:
        failures.append("clean fixture contained an unexpected finding")
    if any(not item["evidence_references"] for item in actual["findings"]):
        failures.append("accepted finding lacks evidence")
    return failures


def _final_predicates(trace: dict[str, Any], actual: dict[str, Any], expected: dict[str, Any]) -> list[str]:
    failures = []
    for field in ("decision", "blocking"):
        if actual[field] != expected[field]:
            failures.append(f"final {field}: expected {expected[field]!r}, got {actual[field]!r}")
    statuses = {item["requirement_id"]: item["status"] for item in trace["entries"]}
    for requirement_id, status in expected["required_requirement_statuses"].items():
        if statuses.get(requirement_id) != status:
            failures.append(f"requirement {requirement_id}: expected {status}, got {statuses.get(requirement_id)}")
    quality = {item["dimension"]: item["status"] for item in actual["quality_summary"]}
    for dimension, status in expected["required_quality_statuses"].items():
        if quality.get(dimension) != status:
            failures.append(f"quality {dimension}: expected {status}, got {quality.get(dimension)}")
    if len(actual["blocking_failures"]) < expected.get("minimum_blocking_failures", 0):
        failures.append("too few blocking failures")
    if expected["forbid_prohibited_outcomes"] and actual["prohibited_outcomes_present"]:
        failures.append("prohibited outcome unexpectedly present")
    return failures


def evaluate_fixtures(root: str | Path) -> dict[str, Any]:
    root = Path(root).resolve()
    cases = []
    metric_counts = defaultdict(lambda: [0, 0])
    for fixture in sorted(path for path in root.iterdir() if path.is_dir()):
        failures: list[str] = []
        manifest_path = fixture / "fixture-manifest.json"
        if not manifest_path.exists():
            continue
        manifest = _load(manifest_path)
        validate(manifest, "fixture-manifest.schema.json")
        if any(path.startswith("expected/") for path in manifest["input_files"]):
            failures.append("expected-result data leaked into analyzer inputs")
        if any(not (fixture / path).exists() for path in manifest["input_files"] + manifest["expected_files"]):
            failures.append("declared fixture input or expected output is missing")
        inputs, expected_root = fixture / "input", fixture / "expected"
        request = _analysis_request(inputs)
        candidates = _load(inputs / "semantic-candidates.json") if (inputs / "semantic-candidates.json").exists() else None
        reviewer = StaticSemanticReviewer(tuple(candidates)) if candidates is not None else None
        decision = analyze(request, reviewer)
        validate_domain_invariants(decision, "analysis_decision")
        if (expected_root / "analysis-predicates.json").exists():
            expected = _load(expected_root / "analysis-predicates.json")
            validate(expected, "expected-analysis-predicates.schema.json")
            analysis_failures = _analysis_predicates(decision, expected)
            failures.extend(analysis_failures)
            category = manifest["scoring_category"]
            metric = (
                "schema_admission_accuracy" if manifest["stage"] == "schema_admission"
                else "false_gap_rejection" if expected["decision"] == "no_material_gap"
                else "true_gap_detection"
            )
            metric_counts[metric][1] += 1
            metric_counts[metric][0] += not analysis_failures
            substantiated = not any(not item["evidence_references"] for item in decision["findings"])
            metric_counts["finding_substantiation"][1] += 1
            metric_counts["finding_substantiation"][0] += substantiated
            consistent = (decision["decision"] == "no_material_gap") == (not decision["blocking"] and decision["execution_ready"] and not decision["findings"])
            metric_counts["decision_consistency"][1] += 1
            metric_counts["decision_consistency"][0] += consistent

        stage_expected = _load(expected_root / "stage-predicates.json") if (expected_root / "stage-predicates.json").exists() else None
        if manifest["stage"] == "gap_closure":
            result = close_findings(
                _load(inputs / "closure-request.json"), prior_decision=_load(inputs / "prior-decision.json"),
                original_plan=_load(inputs / "original-plan.json"), revised_analysis_request=_load(inputs / "revised-analysis-request.json"),
            )
            actual_statuses = [item["status"] for item in result["finding_results"]]
            closure_ok = actual_statuses == stage_expected["statuses"]
            if not closure_ok:
                failures.append(f"closure statuses: expected {stage_expected['statuses']}, got {actual_statuses}")
            metric_counts["closure_accuracy"][1] += 1
            metric_counts["closure_accuracy"][0] += closure_ok
        elif manifest["stage"] == "drift":
            event = compare_baselines(_load(inputs / "locked-baseline.json"), _load(inputs / "current-baseline.json"))
            validate(event, "drift-event.schema.json")
            drift = classify(event, affected_requirements=["requirement.durable-output"])
            validate(drift, "drift-decision.schema.json")
            if drift["decision"] != stage_expected["decision"] or drift["blocking"] != stage_expected["blocking"]:
                failures.append("drift classification mismatch")
        elif manifest["stage"] == "final_evaluation":
            build = _load(inputs / "build-result.json")
            trace, final = evaluate_result(
                locked_plan=_load(inputs / "locked-plan.json"), success_definition=request["success_definition"],
                locked_baseline=_load(inputs / "locked-baseline.json"), post_build_baseline=_load(inputs / "post-build-baseline.json"),
                plan=request["plan"], build_result=build, evidence=_load(inputs / "build-evidence.json"),
            )
            expected = _load(expected_root / "final-evaluation-predicates.json")
            validate(expected, "expected-final-predicates.schema.json")
            final_failures = _final_predicates(trace, final, expected)
            if stage_expected and not set(stage_expected.get("required_prohibited_outcomes", [])).issubset(final["prohibited_outcomes_present"]):
                final_failures.append("required prohibited outcome was not detected")
            failures.extend(final_failures)
            metric_counts["output_quality_evaluation_accuracy"][1] += 1
            metric_counts["output_quality_evaluation_accuracy"][0] += not final_failures
        elif manifest["fixture_id"] == "fixture.unborn-untracked-baseline":
            base = request["baseline"]
            for field in ("git_state", "dirty_state", "git_head"):
                if base[field] != stage_expected[field]:
                    failures.append(f"unborn baseline {field} mismatch")
        cases.append({"fixture_id": manifest["fixture_id"], "passed": not failures, "failures": failures})

    metrics = {}
    for metric in METRICS:
        passed, total = metric_counts[metric]
        metrics[metric] = {"passed": passed, "total": total, "accuracy": 1.0 if total == 0 else passed / total}
    overall = all(case["passed"] for case in cases) and bool(cases)
    metrics["overall_result"] = {"passed": sum(case["passed"] for case in cases), "total": len(cases), "accuracy": 1.0 if overall else (sum(case["passed"] for case in cases) / len(cases) if cases else 0.0)}
    return {"passed": overall, "fixture_count": len(cases), "cases": cases, "metrics": metrics}
