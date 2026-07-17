from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Sequence

from aflow.analysis.decision_engine import analyze
from aflow.analysis.schema_admission import admit
from aflow.adapters.semantic_reviewer import StaticSemanticReviewer
from aflow.drift.compare import compare_baselines
from aflow.drift.classification import classify
from aflow.evaluation.final_decision import evaluate_result
from aflow.evaluation.fixture_evaluator import evaluate_fixtures
from aflow.fixtures.factory import build_result, evidence, final_evidence, fixed_clock, request_bundle
from aflow.lifecycle.closure import close_findings
from aflow.lifecycle.locking import lock_plan
from aflow.storage.hashing import artifact_ref


EXIT_OK = 0
EXIT_SCHEMA_INVALID = 2
EXIT_BLOCKING = 3
EXIT_INTERNAL = 4
EXIT_FIXTURE_FAILURE = 5
EXIT_UNPROVEN = 6


def _load(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("CLI artifact root must be a JSON object")
    return value


def _print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))


def _analyze(path: str) -> int:
    request = _load(path)
    admission = admit(request)
    decision = analyze(request)
    _print({"schema_admission": admission, "decision": decision})
    if not admission["valid"]:
        return EXIT_SCHEMA_INVALID
    return EXIT_OK if decision["execution_ready"] else EXIT_BLOCKING


def _close(path: str) -> int:
    closure = _load(path)
    root = Path(path).resolve().parent
    semantic_path = root / "semantic-candidates.json"
    reviewer = None
    if semantic_path.exists():
        candidates = json.loads(semantic_path.read_text(encoding="utf-8"))
        reviewer = StaticSemanticReviewer(tuple(candidates))
    result = close_findings(
        closure, prior_decision=_load(root / "prior-decision.json"),
        original_plan=_load(root / "original-plan.json"),
        revised_analysis_request=_load(root / "revised-analysis-request.json"),
        reviewer=reviewer,
    )
    _print(result)
    return EXIT_OK if result["new_decision"]["execution_ready"] else EXIT_BLOCKING


def _evaluate(path: str) -> int:
    build = _load(path)
    root = Path(path).resolve().parent
    trace, final = evaluate_result(
        locked_plan=_load(root / "locked-plan.json"), success_definition=_load(root / "success-definition.json"),
        locked_baseline=_load(root / "locked-baseline.json"), post_build_baseline=_load(root / "post-build-baseline.json"),
        plan=_load(root / "plan.json"), build_result=build,
        evidence=json.loads((root / "build-evidence.json").read_text(encoding="utf-8")),
    )
    _print({"trace": trace, "evaluation": final})
    return EXIT_OK if final["decision"] == "proven" else EXIT_UNPROVEN


def demo() -> dict[str, Any]:
    clean = request_bundle()
    clean_decision = analyze(clean, clock=fixed_clock)
    gap = request_bundle()
    gap["analysis_id"] = "analysis.demo-gap"
    gap["plan"]["validations"][0]["required_environment"] = "same-process-test"
    gap["evidence"][0]["claim"] = "Only same-process persistence is visible; restart recovery is absent."
    from aflow.storage.hashing import seal
    gap["evidence"][0] = seal(gap["evidence"][0])
    gap_decision = analyze(gap, clock=fixed_clock)
    revised = deepcopy(gap)
    revised["analysis_id"] = "analysis.demo-revised"
    revised["plan"]["version"] = "1.0.1"
    revised["plan"]["validations"][0]["required_environment"] = "production-host"
    closure_evidence = evidence("evidence.demo-closure", "artifact", "Production-host restart evidence is supplied.", visibility="closure_input")
    revised["evidence"].append(closure_evidence)
    closure_request = {
        "schema_version": "1.0.0", "closure_id": "closure.demo",
        "prior_decision_reference": artifact_ref(gap_decision, "analysis-decision.schema.json", id_field="analysis_id"),
        "original_plan_reference": artifact_ref(gap["plan"], "plan.schema.json", id_field="plan_id"),
        "revised_plan": revised["plan"], "added_evidence": [closure_evidence],
    }
    closure = close_findings(
        closure_request, prior_decision=gap_decision, original_plan=gap["plan"],
        revised_analysis_request=revised, clock=fixed_clock,
    )
    pre_handoff_drift = classify(compare_baselines(revised["baseline"], revised["baseline"], clock=fixed_clock))
    if pre_handoff_drift["blocking"]:
        raise RuntimeError("demo pre-handoff drift unexpectedly blocks")
    locked = lock_plan(revised, closure["new_decision"], clock=fixed_clock)
    weak_evidence = final_evidence()
    weak_build = build_result(locked, revised["baseline"], weak_evidence, environment_matches=False)
    _, weak_final = evaluate_result(
        locked_plan=locked, success_definition=revised["success_definition"], plan=revised["plan"],
        locked_baseline=revised["baseline"], post_build_baseline=revised["baseline"],
        build_result=weak_build, evidence=weak_evidence, clock=fixed_clock,
    )
    strong_evidence = final_evidence()
    strong_build = build_result(locked, revised["baseline"], strong_evidence, environment_matches=True)
    _, strong_final = evaluate_result(
        locked_plan=locked, success_definition=revised["success_definition"], plan=revised["plan"],
        locked_baseline=revised["baseline"], post_build_baseline=revised["baseline"],
        build_result=strong_build, evidence=strong_evidence, clock=fixed_clock,
    )
    return {"steps": [
        {"step": 1, "label": "clean plan", "artifact": clean["plan"]["plan_id"]},
        {"step": 2, "label": "clean decision", "decision": clean_decision["decision"]},
        {"step": 3, "label": "real evidence gap", "decision": gap_decision["decision"]},
        {"step": 4, "label": "revision closes gap", "statuses": [item["status"] for item in closure["finding_results"]]},
        {"step": 5, "label": "locked plan", "lock_id": locked["lock_id"]},
        {"step": 6, "label": "passing tests but insufficient environment", "matches_required_environment": False},
        {"step": 7, "label": "insufficient final result", "decision": weak_final["decision"]},
        {"step": 8, "label": "improved evidence", "matches_required_environment": True},
        {"step": 9, "label": "proven final result", "decision": strong_final["decision"]},
    ]}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aflow")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("analyze", "close", "evaluate-result"):
        item = sub.add_parser(command)
        item.add_argument("artifact")
    fixtures = sub.add_parser("evaluate-fixtures")
    fixtures.add_argument("fixtures_root")
    sub.add_parser("demo")
    args = parser.parse_args(argv)
    try:
        if args.command == "analyze":
            return _analyze(args.artifact)
        if args.command == "close":
            return _close(args.artifact)
        if args.command == "evaluate-result":
            return _evaluate(args.artifact)
        if args.command == "evaluate-fixtures":
            result = evaluate_fixtures(args.fixtures_root)
            _print(result)
            return EXIT_OK if result["passed"] else EXIT_FIXTURE_FAILURE
        result = demo()
        _print(result)
        return EXIT_OK if result["steps"][-1]["decision"] == "proven" else EXIT_INTERNAL
    except Exception as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return EXIT_INTERNAL


if __name__ == "__main__":
    raise SystemExit(main())
