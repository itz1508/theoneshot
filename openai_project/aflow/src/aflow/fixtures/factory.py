from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aflow.analysis.decision_engine import analyze
from aflow.lifecycle.locking import lock_plan
from aflow.storage.atomic_write import atomic_write_json
from aflow.storage.hashing import artifact_ref, canonical_hash, seal


FIXED_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)
SCHEMA_VERSION = "1.0.0"
ALL_QUALITY = [
    "correctness", "completeness", "usability", "consistency", "reliability",
    "evidence_quality", "constraint_compliance",
]


def fixed_clock() -> datetime:
    return FIXED_TIME


def _source(source_id: str, *, status: str = "verified") -> dict[str, Any]:
    return {"source_type": "user_intent", "source_id": source_id, "location": "fixture", "evidence_status": status}


def evidence(
    evidence_id: str, evidence_type: str, claim: str, *,
    visibility: str = "analysis_input", summary: str = "", status: str = "verified",
) -> dict[str, Any]:
    item = {
        "schema_version": SCHEMA_VERSION, "evidence_id": evidence_id, "evidence_type": evidence_type,
        "claim": claim, "source": _source(f"source.{evidence_id}", status=status),
        "collected_by": "deterministic_local", "collected_at": "2026-01-01T00:00:00Z",
        "visibility": visibility, "locator": {"kind": "artifact_id", "value": evidence_id},
        "payload_summary": summary,
    }
    return seal(item)


def success_definition() -> dict[str, Any]:
    value = {
        "schema_version": SCHEMA_VERSION,
        "success_definition_id": "success.fixture",
        "version": "1.0.0",
        "success_statement": "The requested durable output exists and is independently proven in its required environment.",
        "actors": [{"actor_id": "actor.codex", "role": "builder", "expected_capability": "Implement the approved plan."}],
        "requirements": [{
            "requirement_id": "requirement.durable-output",
            "statement": "Produce durable output with the required observable behavior.",
            "observable_outcome": "The output survives restart and behaves correctly at the production host boundary.",
            "quality_dimensions": ALL_QUALITY,
            "priority": "blocking",
            "source_references": [_source("source.user")],
        }],
        "prohibited_outcomes": [{"outcome_id": "outcome.data-loss", "statement": "Existing user data is deleted.", "blocking": True}],
        "non_goals": [{"non_goal_id": "nongoal.container", "statement": "Container execution is outside A-Flow."}],
        "proof_obligations": [{
            "proof_id": "proof.production-observation", "requirement_ids": ["requirement.durable-output"],
            "proof_type": "artifact", "executor": "codex",
            "procedure": "Observe the produced artifact and restart behavior at the production host boundary.",
            "pass_condition": "Artifact and restart evidence prove every declared quality dimension.",
            "fail_condition": "The artifact is absent, substituted, contradicted, or quality evidence is incomplete.",
            "required_environment": "production-host", "required_evidence_types": ["artifact", "test_result"],
        }],
        "confirmation": {"confirmed_by": "user", "confirmed_at": "2026-01-01T00:00:00Z", "content_hash": ""},
    }
    value["confirmation"]["content_hash"] = canonical_hash(value)
    return value


def baseline(*, git_state: str = "unborn", dirty_state: str = "untracked") -> dict[str, Any]:
    return seal({
        "schema_version": SCHEMA_VERSION, "baseline_id": "baseline.fixture", "repository_root": "C:\\fixture-repository",
        "repository_kind": "filesystem", "git_head": None, "git_state": git_state, "dirty_state": dirty_state,
        "scope": {"mode": "scoped", "relevant_paths": ["src"], "protected_paths": ["AGENTS.md"]},
        "entries": [{"path": "src/output.txt", "content_hash": canonical_hash("initial"), "classification": "relevant"}],
        "authority_hashes": [{"authority_id": "authority.user", "content_hash": canonical_hash("authority")}],
        "captured_at": "2026-01-01T00:00:00Z",
    })


def authority() -> dict[str, Any]:
    return seal({
        "schema_version": SCHEMA_VERSION, "authority_bundle_id": "authority.fixture", "repository_root": "C:\\fixture-repository",
        "sources": [{
            "authority_id": "authority.user", "authority_type": "user_instruction", "path": "fixture:user",
            "scope": "src/**", "precedence": 0, "content_hash": canonical_hash("authority"), "evidence_status": "verified",
        }],
        "capabilities": [{
            "capability_id": "capability.codex-write", "description": "Codex may implement files in the target.",
            "owner": "codex", "available": True,
            "activation": {"activation_type": "phase_entry", "required_phase": "implementation", "currently_authorized": True, "source_authority_id": "authority.user", "prerequisites": ["plan accepted"]},
        }],
    })


def repository_evidence() -> dict[str, Any]:
    return seal({
        "schema_version": SCHEMA_VERSION, "repository_evidence_id": "repository.fixture", "repository_root": "C:\\fixture-repository",
        "repository_kind": "filesystem", "state": {"git_head": None, "git_state": "unborn", "dirty_state": "untracked"},
        "entries": [{"path": "src/output.txt", "entry_type": "file", "active_status": "active", "content_hash": canonical_hash("initial"), "evidence_status": "verified", "summary": "Simulated fixture output path."}],
        "captured_at": "2026-01-01T00:00:00Z",
    })


def plan(success: dict[str, Any], base: dict[str, Any], auth: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION, "plan_id": "plan.fixture", "version": "1.0.0", "created_by": "codex",
        "success_definition_reference": artifact_ref(success, "success-definition.schema.json", id_field="success_definition_id"),
        "objective": "Produce and prove the locked durable output.",
        "interpretation": "Implement only the bounded target and prove the production-host observable outcome.",
        "repository_baseline_reference": artifact_ref(base, "baseline.schema.json", id_field="baseline_id"),
        "authority_bundle_reference": artifact_ref(auth, "authority-evidence.schema.json", id_field="authority_bundle_id"),
        "target_paths": [{"path": "src/output.txt", "reason": "Approved output", "authority_status": "allowed"}],
        "excluded_paths": [{"path": "private", "reason": "Outside scope", "authority_status": "prohibited"}],
        "requirements_coverage": [{"requirement_id": "requirement.durable-output", "action_ids": ["action.write-output"], "validation_ids": ["validation.production"]}],
        "actions": [{
            "action_id": "action.write-output", "objective": "Create durable output.",
            "requirement_ids": ["requirement.durable-output"], "capability_id": "capability.codex-write", "owner": "codex",
            "phase": "implementation", "target_paths": ["src/output.txt"], "depends_on": [],
            "prerequisites": [{"prerequisite_id": "prerequisite.accepted", "condition": "Plan is accepted.", "status": "satisfied"}],
            "activation": {"activation_type": "phase_entry", "activation_source": "authority.user", "currently_authorized": True},
            "expected_outputs": [{"output_id": "output.durable", "description": "Durable production output.", "artifact_path": "src/output.txt"}],
        }],
        "validations": [{
            "validation_id": "validation.production", "requirement_ids": ["requirement.durable-output"],
            "action_ids": ["action.write-output"], "proof_id": "proof.production-observation", "proof_type": "artifact", "executor": "codex",
            "procedure": "Inspect artifact and restart behavior at the production host boundary.",
            "pass_condition": "Output remains visible and correct after restart.", "fail_condition": "Output is absent or only substitute behavior was tested.",
            "required_environment": "production-host", "evidence_expected": ["artifact", "test_result"],
        }],
        "dependencies": [{"dependency_id": "dependency.none", "description": "No external dependency is required.", "source": _source("source.user"), "status": "not_required"}],
        "assumptions": [],
        "authority_risks": [{"risk_id": "risk.none", "description": "No unresolved authority risk.", "status": "none_identified"}],
        "failure_handling": [{"failure_id": "failure.proof", "condition": "Required proof is absent.", "action": "return_to_codex", "recovery_condition": "Matching proof is supplied."}],
        "stop_conditions": [{"stop_id": "stop.authority", "condition": "Authority changes.", "effect": "stop_execution", "resume_condition": "Reanalysis succeeds."}],
    }


def analysis_evidence(*, environment_claim: str = "Production-host evidence is available.") -> list[dict[str, Any]]:
    return [evidence("evidence.analysis", "authority_document", environment_claim)]


def request_bundle() -> dict[str, Any]:
    success, base, auth, repo = success_definition(), baseline(), authority(), repository_evidence()
    value_plan = plan(success, base, auth)
    return {
        "schema_version": SCHEMA_VERSION, "analysis_id": "analysis.fixture", "success_definition": success,
        "plan": value_plan, "authority_evidence": auth, "repository_evidence": repo,
        "baseline": base, "evidence": analysis_evidence(),
    }


def final_evidence(*, quality: dict[str, str] | None = None, contradicted: bool = False) -> list[dict[str, Any]]:
    statuses = {dimension: "pass" for dimension in ALL_QUALITY}
    statuses.update(quality or {})
    summary = " ".join(f"quality:{key}={value}" for key, value in statuses.items())
    return [
        evidence("evidence.artifact", "artifact", "The production artifact is directly visible after restart.", visibility="build_result_input", summary=summary, status="contradicted" if contradicted else "verified"),
        evidence("evidence.test", "test_result", "Production-host validation completed.", visibility="build_result_input", summary="production-host boundary exercised"),
    ]


def build_result(locked: dict[str, Any], base: dict[str, Any], evidence_items: list[dict[str, Any]], *,
                 environment_matches: bool = True, prohibited: bool = False, status: str = "completed") -> dict[str, Any]:
    artifact_ev = next(item for item in evidence_items if item["evidence_type"] == "artifact")
    test_ev = next(item for item in evidence_items if item["evidence_type"] == "test_result")
    ref = lambda item: {"evidence_id": item["evidence_id"], "content_hash": item["content_hash"]}
    return seal({
        "schema_version": SCHEMA_VERSION, "build_result_id": "build.fixture",
        "locked_plan_reference": artifact_ref(locked, "locked-plan.schema.json", id_field="lock_id"),
        "produced_by": "codex", "started_at": "2026-01-01T00:00:00Z", "finished_at": "2026-01-01T00:01:00Z", "status": status,
        "observed_environment": {"environment_id": "environment.production" if environment_matches else "environment.mock", "description": "Production host" if environment_matches else "Mocked substitute runtime", "matches_required_environment": environment_matches},
        "outputs": [{"output_id": "output.durable", "requirement_ids": ["requirement.durable-output"], "description": "Observed durable output.", "evidence_references": [ref(artifact_ev)]}],
        "validation_results": [{"validation_id": "validation.production", "status": "passed" if status != "failed" else "failed", "evidence_references": [ref(test_ev)]}],
        "prohibited_outcome_checks": [{"outcome_id": "outcome.data-loss", "present": prohibited, "evidence_references": [ref(artifact_ev)]}],
        "post_build_baseline_reference": artifact_ref(base, "baseline.schema.json", id_field="baseline_id"),
    })


def _expected_analysis(fixture_id: str, decision: str, gap_types: list[str] | None = None, *,
                       locations: list[str] | None = None, rejected: int = 0) -> dict[str, Any]:
    clean = decision == "no_material_gap"
    gaps = gap_types or []
    return {
        "schema_version": SCHEMA_VERSION, "fixture_id": fixture_id, "decision": decision,
        "blocking": not clean, "execution_ready": clean,
        "finding_count": {"min": 0 if clean else 1, "max": 0 if clean else max(1, len(gaps) + 1)},
        "required_gap_types": gaps, "required_plan_locations": locations or [],
        "minimum_rejected_findings": rejected, "forbid_unexpected_findings": clean,
    }


def _expected_final(fixture_id: str, decision: str, status: str, *, quality: dict[str, str] | None = None,
                    prohibited_forbidden: bool = True) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION, "fixture_id": fixture_id, "decision": decision,
        "blocking": decision in {"unproven", "contradicted", "invalidated_by_drift"},
        "required_requirement_statuses": {"requirement.durable-output": status},
        "required_quality_statuses": quality or {},
        "minimum_blocking_failures": 0 if decision in {"proven", "partially_proven"} else 1,
        "forbid_prohibited_outcomes": prohibited_forbidden,
    }


def fixture_specs() -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []

    def add(fixture_id: str, stage: str, category: str, request: dict[str, Any], expected_analysis: dict[str, Any] | None = None,
            *, final_case: dict[str, Any] | None = None, semantic_candidates: list[dict[str, Any]] | None = None,
            closure: dict[str, Any] | None = None, drift: dict[str, Any] | None = None, extra_expected: dict[str, Any] | None = None) -> None:
        specs.append(locals().copy())

    clean = request_bundle()
    add("fixture.01-complete-and-sound", "plan_analysis", "true_negative", clean,
        _expected_analysis("fixture.01-complete-and-sound", "no_material_gap"))

    missing = request_bundle()
    del missing["plan"]["actions"][0]["activation"]
    add("fixture.02-missing-schema-field", "schema_admission", "true_positive_deterministic", missing,
        _expected_analysis("fixture.02-missing-schema-field", "material_gap_found", ["structural_missing"], locations=["/plan/actions/0/activation"]))

    unsupported = request_bundle()
    unsupported["plan"]["validations"][0]["required_environment"] = "same-process-test"
    unsupported["evidence"] = analysis_evidence(environment_claim="The visible test proves persistence only within the same process; restart recovery is not exercised.")
    add("fixture.03-unsupported-assumption", "plan_analysis", "true_positive_semantic", unsupported,
        _expected_analysis("fixture.03-unsupported-assumption", "missing_evidence", ["output_environment_gap"], locations=["/plan/validations/0/required_environment"]))

    invented = request_bundle()
    retry = "one initial attempt plus up to three retries, maximum four total calls, with delays of 1s, 2s, and 4s"
    invented["success_definition"]["success_statement"] = f"Retry behavior is exactly {retry}."
    invented["success_definition"]["requirements"][0]["statement"] = f"Implement {retry}."
    invented["success_definition"]["requirements"][0]["observable_outcome"] = "A retry trace shows at most four calls and backoff delays 1s, 2s, and 4s in order."
    invented["success_definition"]["proof_obligations"][0]["procedure"] = "Inspect an isolated retry trace for one initial call and at most three retries."
    invented["success_definition"]["proof_obligations"][0]["pass_condition"] = "The trace has at most four calls and retry delays are exactly 1s, 2s, and 4s."
    invented["success_definition"]["confirmation"]["content_hash"] = canonical_hash(invented["success_definition"])
    invented["plan"]["success_definition_reference"] = artifact_ref(invented["success_definition"], "success-definition.schema.json", id_field="success_definition_id")
    invented["plan"]["actions"][0]["objective"] = f"Implement {retry}."
    invented["plan"]["validations"][0]["procedure"] = "Inspect the isolated retry trace for the exact call ceiling and delay sequence."
    invented["plan"]["validations"][0]["pass_condition"] = "The trace has one initial call, no more than three retries, and delays 1s, 2s, 4s."
    invented["evidence"] = [evidence("evidence.analysis", "authority_document", f"The plan and proof consistently define {retry}.")]
    false_evidence = invented["evidence"][0]
    false = {
        "schema_version": SCHEMA_VERSION, "finding_id": "finding.false-retry-count", "gap_type": "validation_evidence_gap",
        "origin": "semantic", "severity": "blocking", "blocking": True,
        "requirement_references": ["requirement.durable-output"], "plan_locations": ["/validations/0"],
        "specific_claim": "Validation might be incomplete", "evidence_references": [{"evidence_id": false_evidence["evidence_id"], "content_hash": false_evidence["content_hash"]}],
        "reasoning": "Only one validation exists.", "why_it_matters": "More tests might be desirable.",
        "required_closure": {"closure_code": "add_sufficient_validation", "description": "Add more tests.", "acceptance_predicates": ["More than one validation exists."]},
        "status": "open",
    }
    add("fixture.04-invented-concern-rejected", "plan_analysis", "false_positive_rejection", invented,
        _expected_analysis("fixture.04-invented-concern-rejected", "no_material_gap", rejected=1), semantic_candidates=[false])

    proven_request = request_bundle()
    proven_decision = analyze(proven_request, clock=fixed_clock)
    proven_lock = lock_plan(proven_request, proven_decision, clock=fixed_clock)
    proven_ev = final_evidence()
    add("fixture.05-fully-proven", "final_evaluation", "output_quality", proven_request,
        _expected_analysis("fixture.05-fully-proven", "no_material_gap"),
        final_case={"locked_plan": proven_lock, "build_result": build_result(proven_lock, proven_request["baseline"], proven_ev), "evidence": proven_ev,
                    "expected": _expected_final("fixture.05-fully-proven", "proven", "proven", quality={dimension: "pass" for dimension in ALL_QUALITY})})

    substitute = request_bundle()
    substitute["plan"]["validations"][0]["required_environment"] = "mock-runtime"
    substitute["evidence"] = analysis_evidence(environment_claim="Tests pass in a mocked runtime; the production host boundary differs and was not exercised.")
    add("fixture.06-passing-tests-dont-prove-it", "plan_analysis", "true_positive_semantic", substitute,
        _expected_analysis("fixture.06-passing-tests-dont-prove-it", "missing_evidence", ["output_environment_gap"]))

    wrong_phase = request_bundle()
    wrong_phase["plan"]["actions"][0]["phase"] = "design"
    add("fixture.wrong-phase", "plan_analysis", "true_positive_deterministic", wrong_phase,
        _expected_analysis("fixture.wrong-phase", "material_gap_found", ["sequence_activation_gap"], locations=["/plan/actions/0"]))

    add("fixture.clean-plan", "plan_analysis", "true_negative", request_bundle(),
        _expected_analysis("fixture.clean-plan", "no_material_gap"))
    add("fixture.false-finding-rejected", "plan_analysis", "false_positive_rejection", request_bundle(),
        _expected_analysis("fixture.false-finding-rejected", "no_material_gap", rejected=1), semantic_candidates=[false])

    quality_ev = final_evidence(quality={"usability": "fail"})
    add("fixture.output-quality-fails", "final_evaluation", "output_quality", proven_request,
        _expected_analysis("fixture.output-quality-fails", "no_material_gap"),
        final_case={"locked_plan": proven_lock, "build_result": build_result(proven_lock, proven_request["baseline"], quality_ev), "evidence": quality_ev,
                    "expected": _expected_final("fixture.output-quality-fails", "contradicted", "contradicted", quality={"usability": "fail"})})

    prohibited_ev = final_evidence()
    add("fixture.prohibited-outcome", "final_evaluation", "output_quality", proven_request,
        _expected_analysis("fixture.prohibited-outcome", "no_material_gap"),
        final_case={"locked_plan": proven_lock, "build_result": build_result(proven_lock, proven_request["baseline"], prohibited_ev, prohibited=True), "evidence": prohibited_ev,
                    "expected": _expected_final("fixture.prohibited-outcome", "contradicted", "contradicted", prohibited_forbidden=False)},
        extra_expected={"required_prohibited_outcomes": ["outcome.data-loss"]})

    contradicted_ev = final_evidence(contradicted=True)
    add("fixture.contradicted-result", "final_evaluation", "output_quality", proven_request,
        _expected_analysis("fixture.contradicted-result", "no_material_gap"),
        final_case={"locked_plan": proven_lock, "build_result": build_result(proven_lock, proven_request["baseline"], contradicted_ev, status="failed"), "evidence": contradicted_ev,
                    "expected": _expected_final("fixture.contradicted-result", "contradicted", "contradicted")})

    drift_current = deepcopy(proven_request["baseline"])
    drift_current["baseline_id"] = "baseline.current"
    drift_current["entries"][0]["content_hash"] = canonical_hash("changed")
    drift_current = seal(drift_current)
    add("fixture.relevant-drift", "drift", "drift", proven_request,
        _expected_analysis("fixture.relevant-drift", "no_material_gap"), drift={"locked": proven_request["baseline"], "current": drift_current},
        extra_expected={"decision": "scoped_revalidation_required", "blocking": True})

    closure_original = request_bundle()
    closure_original["plan"]["validations"][0]["required_environment"] = "same-process-test"
    closure_original["evidence"] = analysis_evidence(environment_claim="Only same-process behavior is visible.")
    prior = analyze(closure_original, clock=fixed_clock)
    revised = deepcopy(closure_original)
    revised["analysis_id"] = "analysis.revised"
    revised["plan"]["version"] = "1.0.1"
    closure_ev = evidence("evidence.closure", "artifact", "Production-host restart evidence is now supplied.", visibility="closure_input")
    revised["evidence"].append(closure_ev)
    closure_request = {
        "schema_version": SCHEMA_VERSION, "closure_id": "closure.ineffective",
        "prior_decision_reference": artifact_ref(prior, "analysis-decision.schema.json", id_field="analysis_id"),
        "original_plan_reference": artifact_ref(closure_original["plan"], "plan.schema.json", id_field="plan_id"),
        "revised_plan": revised["plan"], "added_evidence": [closure_ev],
    }
    add("fixture.ineffective-revision", "gap_closure", "closure", closure_original,
        _expected_analysis("fixture.ineffective-revision", "missing_evidence", ["output_environment_gap"]),
        closure={"request": closure_request, "prior": prior, "original_plan": closure_original["plan"], "revised_request": revised},
        extra_expected={"statuses": ["open"]})

    unborn = request_bundle()
    add("fixture.unborn-untracked-baseline", "plan_analysis", "true_negative", unborn,
        _expected_analysis("fixture.unborn-untracked-baseline", "no_material_gap"),
        extra_expected={"git_state": "unborn", "dirty_state": "untracked", "git_head": None})

    improved = deepcopy(closure_original)
    improved["analysis_id"] = "analysis.improved"
    improved["plan"]["version"] = "1.0.1"
    improved["plan"]["validations"][0]["required_environment"] = "production-host"
    improved["evidence"].append(closure_ev)
    improved_request = deepcopy(closure_request)
    improved_request["closure_id"] = "closure.effective"
    improved_request["revised_plan"] = improved["plan"]
    add("fixture.effective-revision", "gap_closure", "closure", closure_original,
        _expected_analysis("fixture.effective-revision", "missing_evidence", ["output_environment_gap"]),
        closure={"request": improved_request, "prior": prior, "original_plan": closure_original["plan"], "revised_request": improved},
        extra_expected={"statuses": ["closed"]})
    return specs


def write_fixtures(root: str | Path) -> list[str]:
    root = Path(root).resolve()
    written: list[str] = []
    for spec in fixture_specs():
        fixture = root / spec["fixture_id"].removeprefix("fixture.")
        inputs, expected = fixture / "input", fixture / "expected"
        input_files = ["input/success-definition.json", "input/plan.json", "input/authority-evidence.json", "input/repository-evidence.json", "input/baseline.json", "input/evidence.json"]
        values = {
            "success-definition.json": spec["request"]["success_definition"], "plan.json": spec["request"]["plan"],
            "authority-evidence.json": spec["request"]["authority_evidence"], "repository-evidence.json": spec["request"]["repository_evidence"],
            "baseline.json": spec["request"]["baseline"], "evidence.json": spec["request"]["evidence"],
        }
        if spec.get("semantic_candidates") is not None:
            values["semantic-candidates.json"] = spec["semantic_candidates"]
            input_files.append("input/semantic-candidates.json")
        if spec.get("final_case"):
            values.update({"locked-plan.json": spec["final_case"]["locked_plan"], "locked-baseline.json": spec["request"]["baseline"], "post-build-baseline.json": spec["request"]["baseline"], "build-result.json": spec["final_case"]["build_result"], "build-evidence.json": spec["final_case"]["evidence"]})
            input_files.extend(["input/locked-plan.json", "input/locked-baseline.json", "input/post-build-baseline.json", "input/build-result.json", "input/build-evidence.json"])
        if spec.get("closure"):
            values.update({"closure-request.json": spec["closure"]["request"], "prior-decision.json": spec["closure"]["prior"], "original-plan.json": spec["closure"]["original_plan"], "revised-analysis-request.json": spec["closure"]["revised_request"]})
            input_files.extend(["input/closure-request.json", "input/prior-decision.json", "input/original-plan.json", "input/revised-analysis-request.json"])
        if spec.get("drift"):
            values.update({"locked-baseline.json": spec["drift"]["locked"], "current-baseline.json": spec["drift"]["current"]})
            input_files.extend(["input/locked-baseline.json", "input/current-baseline.json"])
        expected_files = []
        if spec.get("expected_analysis"):
            atomic_write_json(expected / "analysis-predicates.json", spec["expected_analysis"], output_root=root)
            expected_files.append("expected/analysis-predicates.json")
        if spec.get("final_case"):
            atomic_write_json(expected / "final-evaluation-predicates.json", spec["final_case"]["expected"], output_root=root)
            expected_files.append("expected/final-evaluation-predicates.json")
        if spec.get("extra_expected"):
            atomic_write_json(expected / "stage-predicates.json", spec["extra_expected"], output_root=root)
            expected_files.append("expected/stage-predicates.json")
        manifest = {
            "schema_version": SCHEMA_VERSION, "fixture_id": spec["fixture_id"],
            "behavioral_principle": f"Deterministic behavioral proof for {spec['fixture_id']}.",
            "stage": spec["stage"], "scoring_category": spec["category"],
            "input_files": input_files, "expected_files": expected_files,
            "safety": {"network": False, "docker": False, "git_mutation": False, "target_mutation": False, "subprocess": False},
        }
        for name, value in values.items():
            atomic_write_json(inputs / name, value, output_root=root)
        atomic_write_json(fixture / "fixture-manifest.json", manifest, output_root=root)
        readme = fixture / "README.md"
        readme.parent.mkdir(parents=True, exist_ok=True)
        readme.write_text(f"# {spec['fixture_id']}\n\nInputs and evaluator-only expected truth are isolated.\n", encoding="utf-8", newline="\n")
        written.append(fixture.name)
    return written
