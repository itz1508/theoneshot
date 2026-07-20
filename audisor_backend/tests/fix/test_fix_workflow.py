import hashlib
from pathlib import Path

import pytest

from audisor_backend.adapters.aflow_fix import AFlowFixAdapter, FixLocalInvocationError, invoke_local_fix
from audisor_backend.controllers.fix_controller import FixController
from audisor_backend.phases.fix.phases import (
    make_scoped_manifest,
    make_statements,
    make_scoped_snapshot,
    pre_simulation,
    qualify_plan,
    verify_fix_output,
)
from audisor_backend.policies.fix.completeness import evaluate_completeness
from audisor_backend.schemas.fix.constants import (
    AFLOW_GAP_CORRECTION_ATTEMPTS,
    COMPLETENESS_THRESHOLD,
    MAX_AFLOW_INVOCATIONS_PER_OPERATION,
    PRESIM_RESCORE_ATTEMPTS,
)
from audisor_backend.schemas.fix.models import (
    AFlowOutputs,
    Finding,
    FindingCheck,
    FixScopedManifest,
    ImplementationPlan,
    PlanStep,
    SuccessDefinition,
    ValidationSpec,
)
from audisor_backend.scanning.scanner import ScanReport


def fixture_data():
    findings = [Finding("F-1", "syntax", "src/app.py", "high", {"line": 2})]
    manifest = FixScopedManifest(["src/app.py"], ["src/app.py"], "input-sha", {"src/app.py": "a" * 64})
    plan = ImplementationPlan([PlanStep("S-1", "repair", "src/app.py", "F-1", None)], ["src/app.py"], False)
    success = SuccessDefinition([FindingCheck("F-1", "test", "python -m pytest", "exit 0")], [ValidationSpec("v1", "python -m pytest", "exit 0")], [], "all checks pass")
    outputs = AFlowOutputs(["repair"], success, success.validations)
    return findings, manifest, plan, outputs


def test_fix_constants_are_exact():
    assert COMPLETENESS_THRESHOLD == 0.9391
    assert AFLOW_GAP_CORRECTION_ATTEMPTS == 1
    assert PRESIM_RESCORE_ATTEMPTS == 1
    assert MAX_AFLOW_INVOCATIONS_PER_OPERATION == 1


def test_plan_qualification_uses_fix_origin_and_advisory_minor_issue():
    findings, manifest, plan, _ = fixture_data()
    qualified = qualify_plan(plan, manifest)
    assert qualified.is_qualified
    assert qualified.steps[0].originating_finding_id == "F-1"
    assert qualified.minor_issues[0].type == "missing_acceptance_criterion"


def test_adapter_invokes_agent_once_and_keeps_model_advisory():
    findings, manifest, plan, _ = fixture_data()
    calls = []
    qualified = qualify_plan(plan, manifest)
    adapter = AFlowFixAdapter(lambda package: calls.append(package) or {"status": "accepted", "plan": qualified, "gap_corrections_applied": 1})
    result = adapter.ignite(qualified, findings, manifest)
    assert result.status == "accepted"
    assert len(calls) == 1
    assert adapter.invocations == 1
    with pytest.raises(RuntimeError):
        adapter.ignite(qualified, findings, manifest)


def test_inspection_requires_exact_output_hashes_and_all_checks():
    result = verify_fix_output(
        {"src/app.py": "before"},
        {"src/app.py": "after"},
        {"src/app.py": "after"},
        {"finding_checks": [{"finding_id": "F-1", "passed": True}], "validations": [], "must_not_regress": []},
        True,
    )
    assert result.verified
    assert result.proof["satisfied"]
    failed = verify_fix_output({"src/app.py": "before"}, {"src/app.py": "wrong"}, {"src/app.py": "after"}, {"finding_checks": [{"passed": True}]}, True)
    assert not failed.verified
    assert failed.reason == "hash proof failed"


def test_structural_gate_beats_high_weighted_score():
    result = evaluate_completeness({"scope_completeness": 1, "dependency_resolvability": 1, "plan_completeness": 0, "aflow_output_completeness": 1, "statement_consistency": 1, "dependency_integrity": 1})
    assert result.score == 1
    assert result.status == "uncorrectable"


def test_conflicting_dependency_finding_blocks_dependency_integrity():
    findings, manifest, plan, outputs = fixture_data()
    statements = make_statements(findings, manifest)
    qualified = qualify_plan(plan, manifest)
    conflict = Finding("dep-1", "dependency.conflicting_constraints", "requirements.txt", "high", {})
    result = pre_simulation(statements, qualified, outputs, manifest, [conflict])
    assert result.dependency_integrity["score"] == 0
    assert result.status == "uncorrectable"
    assert result.dependency_integrity["flagged_issues"][0]["code"] == -10
    assert result.dependency_integrity["flagged_issues"][0]["type"] == "version_mismatch"


def test_scoped_snapshot_contains_only_issue_closure_and_hashes(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("broken = True\n", encoding="utf-8")
    (tmp_path / "src" / "dep.py").write_text("dependency = True\n", encoding="utf-8")
    (tmp_path / "unaffected.py").write_text("untouched = True\n", encoding="utf-8")
    findings = [Finding("F-1", "syntax", "src/app.py", "high", {})]
    snapshot = make_scoped_snapshot(tmp_path, findings, ["src/app.py", "src/dep.py"], tmp_path / "artifacts")
    assert snapshot.files == ("src/app.py",)
    assert set(snapshot.file_hashes) == {"src/app.py", "src/dep.py"}
    assert (Path(snapshot.storage_path) / "src" / "app.py").is_file()
    assert not (Path(snapshot.storage_path) / "unaffected.py").exists()
    snapshot.manifest().validate()


def test_controller_scan_and_prepare_persists_only_scanner_affected_items(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("import missing_package\n", encoding="utf-8")
    (tmp_path / "unaffected.py").write_text("value = 1\n", encoding="utf-8")
    controller = FixController()
    report, snapshot, manifest, statements = controller.scan_and_prepare(tmp_path, tmp_path / "fix-artifacts")
    assert report.findings
    assert "src/app.py" in manifest.files
    assert "unaffected.py" not in manifest.dependency_closure
    assert Path(snapshot.storage_path).is_dir()
    assert all(statement.manifest_ref_hash for statement in statements)


def test_scoped_snapshot_resolves_direct_local_import_with_evidence(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("from .helper import value\nprint(value)\n", encoding="utf-8")
    (tmp_path / "src" / "helper.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "unrelated.py").write_text("value = 2\n", encoding="utf-8")
    finding = Finding("F-1", "correctness.undefined_symbol", "src/app.py", "high", {})
    controller = FixController()
    _report, _snapshot, manifest, _statements = controller.scan_and_prepare(tmp_path, tmp_path / "artifacts", scanner=type("Scanner", (), {"scan": lambda self, root: ScanReport([finding], ("src/app.py",))})())
    assert "src/helper.py" in manifest.dependency_closure
    assert manifest.dependency_evidence["src/helper.py"][0]["originating_finding_id"] == "F-1"
    assert "unrelated.py" not in manifest.dependency_closure


def test_controller_stops_before_aflow_without_scoped_snapshot():
    findings = [Finding("F-1", "syntax", "src/app.py", "high", {})]
    manifest = make_scoped_manifest(findings, ["src/app.py"], "input-sha")
    plan = ImplementationPlan([PlanStep("S-1", "repair", "src/app.py", "F-1", "test")], ["src/app.py"], True)
    calls = []
    adapter = AFlowFixAdapter(lambda _package: calls.append(True))
    result = FixController().run(findings, manifest, plan, (), adapter, None, None)
    assert result.unresolved_reason == "information_gap"
    assert result.quality_notes["reason"] == "scoped_snapshot_required"
    assert calls == []


def test_run_from_scan_persists_snapshot_before_aflow(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    finding = Finding("F-1", "security.hardcoded_secret", "src/app.py", "high", {})
    class Scanner:
        def scan(self, _root):
            return ScanReport([finding], ("src/app.py",))
    plan = ImplementationPlan([PlanStep("S-1", "repair", "src/app.py", "F-1", "test passes")], ["src/app.py"], True)
    success = SuccessDefinition([FindingCheck("F-1", "test", "pytest", "exit 0")], [ValidationSpec("v1", "pytest", "exit 0")], [], "all checks pass")
    outputs = AFlowOutputs([], success, success.validations)
    calls = []
    adapter = AFlowFixAdapter(lambda package: calls.append(package) or {"status": "accepted", "plan": plan, "gap_corrections_applied": 0})
    inspection = verify_fix_output({"src/app.py": "before"}, {"src/app.py": "after"}, {"src/app.py": "after"}, {"finding_checks": [{"passed": True}]}, True)
    result, _report, snapshot, manifest, _statements = FixController().run_from_scan(tmp_path, tmp_path / "artifacts", plan, adapter, outputs, inspection, scanner=Scanner())
    assert len(calls) == 1
    assert calls[0]["manifest"].file_hashes
    assert result.released
    assert snapshot.manifest().input_hash == manifest.input_hash


def test_controller_releases_only_after_validated_inspection():
    findings, manifest, plan, outputs = fixture_data()
    statements = make_statements(findings, manifest)
    qualified = qualify_plan(plan, manifest)
    adapter = AFlowFixAdapter(lambda package: {"status": "accepted", "plan": qualified, "gap_corrections_applied": 0})
    inspection = verify_fix_output({"src/app.py": "before"}, {"src/app.py": "after"}, {"src/app.py": "after"}, {"finding_checks": [{"passed": True}], "validations": [], "must_not_regress": []}, True)
    result = FixController("automatic").run(findings, manifest, plan, statements, adapter, outputs, inspection)
    assert result.released
    assert result.resolved_items == ["F-1"]


def test_manual_skip_is_unresolved_user_skip():
    findings, manifest, plan, outputs = fixture_data()
    statements = make_statements(findings, manifest)
    qualified = qualify_plan(plan, manifest)
    adapter = AFlowFixAdapter(lambda package: {"status": "accepted", "plan": qualified, "gap_corrections_applied": 0})
    inspection = verify_fix_output({"src/app.py": "before"}, {"src/app.py": "after"}, {"src/app.py": "after"}, {"finding_checks": [{"passed": True}], "validations": [], "must_not_regress": []}, True)
    result = FixController("manual").run(findings, manifest, plan, statements, adapter, outputs, inspection, user_decision="skip")
    assert not result.released
    assert result.unresolved_reason == "user_skip"


def test_local_fix_boundary_invokes_worker_once_and_preserves_scope(tmp_path):
    findings, manifest, plan, _ = fixture_data()
    class Worker:
        structured_output = False
        calls = 0

        def execute(self, _task):
            self.calls += 1
            return type("Response", (), {"answer": '{"gap_corrections_applied":0,"plan":{"steps":[{"acceptance_criterion":"test passes","action":"repair","id":"S-1","originating_finding_id":"F-1","target_file":"src/app.py"}],"target_files":["src/app.py"]},"status":"accepted","success_definition":{"finding_checks":[{"finding_id":"F-1","resolution_method":"rescan","check":"scanner_clear::F-1","expected_result":"finding resolved"}],"validations":[{"id":"V-1","command_or_assertion":"python_compiles::src/app.py","expected_result":"compiles without error"}],"must_not_regress":["tests pass"],"success_rule":"all_finding_checks_and_validations_pass"}}'})()

    worker = Worker()
    result = invoke_local_fix(worker, qualify_plan(plan, manifest), findings, manifest, repository_root=tmp_path)
    assert worker.calls == 1
    assert worker.structured_output is True
    assert result.implementation_eligible is True
    assert result.candidate_plan.steps[0].originating_finding_id == "F-1"
    assert result.execution_contract is None
    assert result.success_definition is not None
    assert result.success_definition.covers(findings)


def test_local_fix_boundary_rejects_fenced_or_out_of_scope_output(tmp_path):
    findings, manifest, plan, _ = fixture_data()
    success_def = '{"success_definition":{"finding_checks":[{"finding_id":"F-1","resolution_method":"rescan","check":"scanner_clear::F-1","expected_result":"ok"}],"validations":[],"must_not_regress":[],"success_rule":"all_finding_checks_and_validations_pass"}}'
    class Worker:
        structured_output = False

        def __init__(self, answer):
            self.answer = answer

        def execute(self, _task):
            return type("Response", (), {"answer": self.answer})()

    with pytest.raises(FixLocalInvocationError) as fenced:
        invoke_local_fix(Worker("```json {} ```"), qualify_plan(plan, manifest), findings, manifest, repository_root=tmp_path)
    assert fenced.value.code == "invalid_response_framing"
    out_of_scope = '{"gap_corrections_applied":0,"plan":{"steps":[{"acceptance_criterion":"test passes","action":"repair","id":"S-1","originating_finding_id":"F-1","target_file":"src/other.py"}],"target_files":["src/other.py"]},"status":"accepted",' + success_def[1:]
    with pytest.raises(FixLocalInvocationError) as scoped:
        invoke_local_fix(Worker(out_of_scope), qualify_plan(plan, manifest), findings, manifest, repository_root=tmp_path)
    assert scoped.value.code == "scope_violation"
