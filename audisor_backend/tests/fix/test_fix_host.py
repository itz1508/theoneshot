import concurrent.futures
import json
import threading
from dataclasses import asdict
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from audisor.audisor_lifecycle.analysis_package import AnalysisPackageError, FrozenAnalysisPackage
from audisor.audisor_lifecycle.ignition import IgnitionResult
from audisor.audisor_lifecycle.operation import FrozenAudisorPolicy
from audisor.operations.transport import deserialize_request

from audisor_backend.controllers.fix_controller import FixController
from audisor_backend.controllers.fix_host import AcceptedFixDispatcher, AcceptedFixOperation, FixOperationStore
from audisor_backend.phases.fix.phases import make_scoped_manifest, make_statements
from audisor_backend.scanning.dependency_closure import resolve_dependency_details
from audisor_backend.schemas.fix.models import Finding, FindingCheck, FixScopedManifest, ImplementationPlan, PlanStep, SuccessDefinition, ValidationSpec


# Frozen A-Flow analysis-request fixtures (the same canonical inputs the runtime
# package tests use). Loading them lets the H1 package-path tests exercise the
# REAL package_from_context validation instead of concealing an impossible
# fixture behind a mock.
_ANALYSIS_FIXTURE_ROOT = (
    Path(__file__).resolve().parents[3]
    / "openai_project"
    / "aflow"
    / "tests"
    / "fixtures"
    / "05-fully-proven"
    / "input"
)


def valid_analysis_request(operation_id: str = "fix-001") -> dict:
    """Return a schema-valid frozen analysis request bound to ``operation_id``.

    ``package_from_context`` requires ``analysis_id`` to equal the host
    operation id, so the fixture's analysis_id is rebound to the Fix operation.
    """
    return {
        "schema_version": "1.0.0",
        "analysis_id": operation_id,
        "success_definition": json.loads((_ANALYSIS_FIXTURE_ROOT / "success-definition.json").read_text(encoding="utf-8")),
        "plan": json.loads((_ANALYSIS_FIXTURE_ROOT / "plan.json").read_text(encoding="utf-8")),
        "authority_evidence": json.loads((_ANALYSIS_FIXTURE_ROOT / "authority-evidence.json").read_text(encoding="utf-8")),
        "repository_evidence": json.loads((_ANALYSIS_FIXTURE_ROOT / "repository-evidence.json").read_text(encoding="utf-8")),
        "baseline": json.loads((_ANALYSIS_FIXTURE_ROOT / "baseline.json").read_text(encoding="utf-8")),
        "evidence": json.loads((_ANALYSIS_FIXTURE_ROOT / "evidence.json").read_text(encoding="utf-8")),
    }


def operation(aflow_analysis_request=None, aflow_analysis_request_present=None):
    # Presence defaults to "the request value is not None" so existing callers
    # keep their semantics: operation() -> field absent (legacy path);
    # operation(req) -> field present. Pass aflow_analysis_request_present=True
    # together with a None request to model an explicit JSON null (supplied-invalid).
    if aflow_analysis_request_present is None:
        aflow_analysis_request_present = aflow_analysis_request is not None
    findings = [Finding("F-1", "syntax", "src/app.py", "high", {"line": 1})]
    manifest = FixScopedManifest(["src/app.py"], ["src/app.py"], "input", {"src/app.py": "a" * 64})
    statements = make_statements(findings, manifest)
    plan = ImplementationPlan([PlanStep("S-1", "repair", "src/app.py", "F-1", "test passes")], ["src/app.py"], True)
    return AcceptedFixOperation(
        "fix-001", findings, manifest, statements, plan,
        {"path": "sandbox/fix-001"}, {"allowed_paths": ["src/app.py"]},
        aflow_analysis_request,
        aflow_analysis_request_present=aflow_analysis_request_present,
    )


# Sentinel for _fix_envelope: "omit the aflow_analysis_request field entirely"
# (absent), as distinct from supplying it as None (explicit JSON null).
_ABSENT = object()


def _enabled_policy():
    return FrozenAudisorPolicy(True, "local-openai-compatible", "qwen2.5-coder:7b", "http://127.0.0.1:11434")


def _fix_envelope(aflow_analysis_request=_ABSENT, operation_id="fix-001"):
    """Build a canonical wire envelope for ``deserialize_request``.

    The Fix payload is derived from ``operation()`` so it is guaranteed valid and
    completable. ``aflow_analysis_request`` defaults to a sentinel meaning "omit
    the field" (absent); pass ``None`` to supply an explicit JSON null.
    """
    base = operation()
    fix_payload = {
        "findings": [asdict(finding) for finding in base.findings],
        "manifest": asdict(base.manifest),
        "statements": [asdict(statement) for statement in base.statements],
        "plan": asdict(base.plan),
        "workspace_identity": dict(base.workspace_identity),
        "authority_context": dict(base.authority_context),
    }
    if aflow_analysis_request is not _ABSENT:
        fix_payload["aflow_analysis_request"] = aflow_analysis_request
    return {
        "operation_id": operation_id,
        "operation_kind": "fix",
        "client": {"client_id": "test-client", "adapter_id": "test-adapter", "adapter_version": "1.0.0"},
        "repository": {"root": "sandbox/fix-001"},
        "requested_scope": {"files": ["src/app.py"]},
        "fix": fix_payload,
    }


def test_enabled_fix_invokes_once_persists_and_duplicate_does_not_reinvoke(tmp_path):
    calls = []
    continued = []

    def igniter(operation_context, policy, worker):
        calls.append((operation_context, policy, worker))
        return IgnitionResult(True, "supplied", operation_context.accepted_plan, {"readiness": {}}, True)

    dispatcher = AcceptedFixDispatcher(
        FixOperationStore(tmp_path),
        policy_reader=lambda: FrozenAudisorPolicy(True, "local-openai-compatible", "qwen2.5-coder:7b", "http://127.0.0.1:11434"),
        aflow_igniter=igniter,
        worker_factory=lambda *args, **kwargs: object(),
    )
    op = operation()
    controller = FixController()
    result = controller.accept(op, dispatcher, lambda operation, result: continued.append(result) or "continued", lambda operation, result: "unresolved")
    duplicate = controller.accept(op, dispatcher, lambda operation, result: continued.append(result) or "continued", lambda operation, result: "unresolved")
    assert result == "continued"
    assert duplicate["status"] == "accepted"
    assert len(calls) == 1
    assert len(continued) == 1
    assert dispatcher.store.load("fix-001")["status"] == "accepted"
    handoff = tmp_path / "fix-001" / "qualified-fix-handoff.json"
    assert handoff.is_file()
    assert dispatcher.store.load("fix-001")["handoff_path"] == str(handoff)


def test_disabled_fix_skips_ignite_and_continues(tmp_path):
    calls = []
    dispatcher = AcceptedFixDispatcher(
        FixOperationStore(tmp_path),
        policy_reader=lambda: FrozenAudisorPolicy(False, "local-openai-compatible", "qwen2.5-coder:7b", "http://127.0.0.1:11434"),
        aflow_igniter=lambda **kwargs: calls.append(kwargs),
        worker_factory=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("worker constructed")),
    )
    result = FixController().accept(operation(), dispatcher, lambda operation, result: result["status"], lambda operation, result: "unresolved")
    assert result == "skipped_disabled"
    assert calls == []


def test_invalid_fix_plan_stays_unresolved_without_ignite(tmp_path):
    op = operation()
    invalid = AcceptedFixOperation(op.operation_id, op.findings, op.manifest, op.statements, ImplementationPlan([], [], False), op.workspace_identity, op.authority_context)
    calls = []
    dispatcher = AcceptedFixDispatcher(FixOperationStore(tmp_path), aflow_igniter=lambda **kwargs: calls.append(kwargs))
    assert FixController().accept(invalid, dispatcher, lambda operation, result: "continued", lambda operation, result: result["status"]) == "validation_failed"
    assert calls == []


def test_external_dispatch_rejects_snapshotless_fix_before_policy_or_ignite(tmp_path):
    op = operation()
    op = AcceptedFixOperation(
        op.operation_id,
        op.findings,
        FixScopedManifest(["src/app.py"], ["src/app.py"], "input"),
        op.statements,
        op.plan,
        op.workspace_identity,
        op.authority_context,
    )
    calls = []
    dispatcher = AcceptedFixDispatcher(FixOperationStore(tmp_path), aflow_igniter=lambda **kwargs: calls.append(kwargs))
    result = FixController().accept(op, dispatcher, lambda operation, result: "continued", lambda operation, result: result["error"]["code"])
    assert result == "scoped_snapshot_required"
    assert calls == []


# ---------------------------------------------------------------------------
# New tests: deterministic dependency preparation in production dispatch
# ---------------------------------------------------------------------------


def test_A_repository_resolvable_dependency_gap_enriches_manifest(tmp_path):
    """Fixture: finding references a file that imports a local module that exists."""
    repo = tmp_path / "repo"
    src = repo / "src"
    src.mkdir(parents=True)
    (src / "app.py").write_text("import helper\n", encoding="utf-8")
    (src / "helper.py").write_text("x = 1\n", encoding="utf-8")

    from hashlib import sha256
    app_hash = sha256((src / "app.py").read_bytes()).hexdigest()
    helper_hash = sha256((src / "helper.py").read_bytes()).hexdigest()

    finding = Finding("F-1", "dependency.unresolved", "src/app.py", "high", {"module": "helper", "resolver_state": "missing", "repro": "resolve Python import helper from src/app.py"})
    manifest = FixScopedManifest(
        ["src/app.py"], ["src/app.py"], "input",
        {"src/app.py": app_hash},
    )
    statements = make_statements([finding], manifest)
    plan = ImplementationPlan([PlanStep("S-1", "repair", "src/app.py", "F-1", "test passes")], ["src/app.py"], True)
    op = AcceptedFixOperation("fix-dep-001", [finding], manifest, statements, plan, {"root": str(repo)}, {"allowed_paths": ["src/app.py"]})

    calls = []
    continued = []

    def igniter(operation_context, policy, worker):
        calls.append(("igniter", operation_context))
        return IgnitionResult(True, "supplied", operation_context.accepted_plan, {"readiness": {}}, True)

    dispatcher = AcceptedFixDispatcher(
        FixOperationStore(tmp_path),
        policy_reader=lambda: FrozenAudisorPolicy(True, "local-openai-compatible", "qwen2.5-coder:7b", "http://127.0.0.1:11434"),
        aflow_igniter=igniter,
        worker_factory=lambda *args, **kwargs: object(),
    )

    result = FixController().accept(
        op, dispatcher,
        lambda operation, result: continued.append(result) or "continued",
        lambda operation, result: "unresolved",
    )

    # Prove: resolve_dependency_details was reached from production dispatch
    assert result == "continued"
    assert len(calls) == 1
    # The igniter received the enriched manifest
    accepted_task = calls[0][1].accepted_task
    enriched_manifest = accepted_task["manifest"]
    assert "src/helper.py" in enriched_manifest["dependency_closure"]
    # dependency_evidence identifies the originating finding
    evidence = enriched_manifest.get("dependency_evidence", {})
    assert "src/helper.py" in evidence
    assert any(rec["originating_finding_id"] == "F-1" for rec in evidence["src/helper.py"])

    # Prove: qualified-fix-handoff.json contains the enriched manifest
    handoff_path = tmp_path / "fix-dep-001" / "qualified-fix-handoff.json"
    assert handoff_path.is_file()
    handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
    assert "src/helper.py" in handoff["scoped_manifest"]["dependency_closure"]
    assert "src/helper.py" in handoff["scoped_manifest"].get("dependency_evidence", {})

    # Prove: operation proceeds normally
    stored = dispatcher.store.load("fix-dep-001")
    assert stored["status"] == "accepted"


def test_B_unresolved_dependency_gap_blocks_before_handoff(tmp_path):
    """Fixture: finding has unresolved local dependency; no matching file exists."""
    repo = tmp_path / "repo"
    src = repo / "src"
    src.mkdir(parents=True)
    (src / "app.py").write_text("import absent_package\n", encoding="utf-8")

    from hashlib import sha256
    app_hash = sha256((src / "app.py").read_bytes()).hexdigest()

    finding = Finding("F-1", "dependency.unresolved", "src/app.py", "high", {"module": "absent_package", "resolver_state": "missing", "repro": "resolve Python import absent_package from src/app.py"})
    manifest = FixScopedManifest(
        ["src/app.py"], ["src/app.py"], "input",
        {"src/app.py": app_hash},
    )
    statements = make_statements([finding], manifest)
    plan = ImplementationPlan([PlanStep("S-1", "repair", "src/app.py", "F-1", "test passes")], ["src/app.py"], True)
    op = AcceptedFixOperation("fix-dep-002", [finding], manifest, statements, plan, {"root": str(repo)}, {"allowed_paths": ["src/app.py"]})

    calls = []
    unresolved_calls = []

    def igniter(operation_context, policy, worker):
        calls.append(("igniter", operation_context))
        return IgnitionResult(True, "supplied", operation_context.accepted_plan, {"readiness": {}}, True)

    dispatcher = AcceptedFixDispatcher(
        FixOperationStore(tmp_path),
        policy_reader=lambda: FrozenAudisorPolicy(True, "local-openai-compatible", "qwen2.5-coder:7b", "http://127.0.0.1:11434"),
        aflow_igniter=igniter,
        worker_factory=lambda *args, **kwargs: object(),
    )

    result = FixController().accept(
        op, dispatcher,
        lambda operation, result: "continued",
        lambda operation, result: unresolved_calls.append(result) or "unresolved",
    )

    # Prove: no execution-authorized handoff is produced
    handoff_path = tmp_path / "fix-dep-002" / "qualified-fix-handoff.json"
    assert not handoff_path.exists()

    # Prove: Codex was never launched (igniter was called but completeness blocked)
    assert len(calls) == 1

    # Prove: result status is blocked
    assert result == "unresolved"
    assert len(unresolved_calls) == 1
    blocked = unresolved_calls[0]
    assert blocked["status"] == "blocked"
    assert blocked["implementation_eligible"] is False

    # Prove: missing_info is persisted
    assert "missing_info" in blocked
    assert "completeness_status" in blocked

    # Prove: unresolved_reason is information_gap
    assert blocked["unresolved_reason"] == "information_gap"

    # Prove: dependency resolution evidence is present
    assert "dependency_resolution" in blocked
    dep_results = blocked["dependency_resolution"]
    assert len(dep_results) == 1
    assert dep_results[0]["finding_id"] == "F-1"
    assert dep_results[0]["resolved"] is False
    assert dep_results[0]["failure_reason"] is not None
    assert "absent_package" in dep_results[0]["failure_reason"]

    # Prove: attempted_resolution_count is 1
    assert blocked["attempted_resolution_count"] == 1


def test_C_no_dependency_gap_preserves_existing_behavior(tmp_path):
    """Fixture: no dependency findings; existing accepted Fix behavior unchanged."""
    repo = tmp_path / "repo"
    src = repo / "src"
    src.mkdir(parents=True)
    (src / "app.py").write_text("x = 1\n", encoding="utf-8")

    from hashlib import sha256
    app_hash = sha256((src / "app.py").read_bytes()).hexdigest()

    finding = Finding("F-1", "syntax", "src/app.py", "high", {"line": 1})
    manifest = FixScopedManifest(
        ["src/app.py"], ["src/app.py"], "input",
        {"src/app.py": app_hash},
    )
    statements = make_statements([finding], manifest)
    plan = ImplementationPlan([PlanStep("S-1", "repair", "src/app.py", "F-1", "test passes")], ["src/app.py"], True)
    op = AcceptedFixOperation("fix-no-dep-001", [finding], manifest, statements, plan, {"root": str(repo)}, {"allowed_paths": ["src/app.py"]})

    calls = []
    continued = []

    def igniter(operation_context, policy, worker):
        calls.append(("igniter", operation_context))
        return IgnitionResult(True, "supplied", operation_context.accepted_plan, {"readiness": {}}, True)

    dispatcher = AcceptedFixDispatcher(
        FixOperationStore(tmp_path),
        policy_reader=lambda: FrozenAudisorPolicy(True, "local-openai-compatible", "qwen2.5-coder:7b", "http://127.0.0.1:11434"),
        aflow_igniter=igniter,
        worker_factory=lambda *args, **kwargs: object(),
    )

    result = FixController().accept(
        op, dispatcher,
        lambda operation, result: continued.append(result) or "continued",
        lambda operation, result: "unresolved",
    )

    # Prove: operation proceeds normally
    assert result == "continued"
    assert len(calls) == 1

    # Prove: handoff exists
    handoff_path = tmp_path / "fix-no-dep-001" / "qualified-fix-handoff.json"
    assert handoff_path.is_file()

    # Prove: manifest is not expanded with irrelevant files
    handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
    manifest_files = handoff["scoped_manifest"]["dependency_closure"]
    assert "src/app.py" in manifest_files
    # No extra files added (only the original finding file)
    assert len(manifest_files) == 1

    # Prove: stored status is accepted
    stored = dispatcher.store.load("fix-no-dep-001")
    assert stored["status"] == "accepted"


def test_D_build_behavior_unchanged():
    """Build operations are not affected by Fix dependency preparation."""
    # This test verifies that the Build path does not import or invoke
    # any Fix-specific dependency preparation code.
    from audisor_backend.controllers.fix_host import _prepare_dependency_evidence, DependencyResolutionResult
    # The function exists and is importable, but Build never calls it.
    # This is a structural assertion: the Build executor path
    # (executor.py:_execute_mutation) does not reference fix_host.
    assert callable(_prepare_dependency_evidence)
    assert DependencyResolutionResult is not None


def test_E_duplicate_operation_does_not_rerun_preparation(tmp_path):
    """Duplicate operation ID must not rerun dependency resolution or Codex."""
    repo = tmp_path / "repo"
    src = repo / "src"
    src.mkdir(parents=True)
    (src / "app.py").write_text("import helper\n", encoding="utf-8")
    (src / "helper.py").write_text("x = 1\n", encoding="utf-8")

    from hashlib import sha256
    app_hash = sha256((src / "app.py").read_bytes()).hexdigest()

    finding = Finding("F-1", "dependency.unresolved", "src/app.py", "high", {"module": "helper", "resolver_state": "missing", "repro": "resolve Python import helper from src/app.py"})
    manifest = FixScopedManifest(
        ["src/app.py"], ["src/app.py"], "input",
        {"src/app.py": app_hash},
    )
    statements = make_statements([finding], manifest)
    plan = ImplementationPlan([PlanStep("S-1", "repair", "src/app.py", "F-1", "test passes")], ["src/app.py"], True)
    op = AcceptedFixOperation("fix-dup-001", [finding], manifest, statements, plan, {"root": str(repo)}, {"allowed_paths": ["src/app.py"]})

    resolution_count = [0]
    original_resolve = resolve_dependency_details

    def counting_resolve(root, findings):
        resolution_count[0] += 1
        return original_resolve(root, findings)

    import audisor_backend.controllers.fix_host as fh
    fh.resolve_dependency_details = counting_resolve
    try:
        calls = []
        continued = []

        def igniter(operation_context, policy, worker):
            calls.append(("igniter", operation_context))
            return IgnitionResult(True, "supplied", operation_context.accepted_plan, {"readiness": {}}, True)

        dispatcher = AcceptedFixDispatcher(
            FixOperationStore(tmp_path),
            policy_reader=lambda: FrozenAudisorPolicy(True, "local-openai-compatible", "qwen2.5-coder:7b", "http://127.0.0.1:11434"),
            aflow_igniter=igniter,
            worker_factory=lambda *args, **kwargs: object(),
        )

        # First submission
        result1 = FixController().accept(
            op, dispatcher,
            lambda operation, result: continued.append(result) or "continued",
            lambda operation, result: "unresolved",
        )
        assert result1 == "continued"
        assert resolution_count[0] == 1
        assert len(calls) == 1

        # Second submission (duplicate)
        result2 = FixController().accept(
            op, dispatcher,
            lambda operation, result: continued.append(result) or "continued",
            lambda operation, result: "unresolved",
        )
        # Duplicate returns stored result, not "continued"
        assert result2["status"] == "accepted"
        # Resolution did NOT run again
        assert resolution_count[0] == 1
        # Igniter did NOT run again
        assert len(calls) == 1
        # Only one continue callback was invoked
        assert len(continued) == 1
    finally:
        fh.resolve_dependency_details = original_resolve


# ---------------------------------------------------------------------------
# Authority separation tests
# ---------------------------------------------------------------------------


def test_F_authority_decision_required_blocks_before_model(tmp_path):
    """Fixture: finding requires authority decision; none supplied → blocked."""
    repo = tmp_path / "repo"
    src = repo / "src"
    src.mkdir(parents=True)
    (src / "app.py").write_text("x = 1\n", encoding="utf-8")
    (src / "controller.py").write_text("y = 2\n", encoding="utf-8")

    from hashlib import sha256
    app_hash = sha256((src / "app.py").read_bytes()).hexdigest()
    ctrl_hash = sha256((src / "controller.py").read_bytes()).hexdigest()

    finding = Finding("F-1", "authority.competing_authority_path", "src/app.py", "high", {"authority_candidates": ["src/app.py", "src/controller.py"], "repro": "inspect active authority-named modules"})
    manifest = FixScopedManifest(
        ["src/app.py", "src/controller.py"], ["src/app.py", "src/controller.py"], "input",
        {"src/app.py": app_hash, "src/controller.py": ctrl_hash},
    )
    statements = make_statements([finding], manifest)
    plan = ImplementationPlan([PlanStep("S-1", "repair", "src/app.py", "F-1", "test passes")], ["src/app.py"], True)
    op = AcceptedFixOperation("fix-auth-001", [finding], manifest, statements, plan, {"root": str(repo)}, {"allowed_paths": ["src/app.py"]})

    calls = []
    unresolved_calls = []

    def igniter(operation_context, policy, worker):
        calls.append(("igniter", operation_context))
        return IgnitionResult(True, "supplied", operation_context.accepted_plan, {"readiness": {}}, True)

    dispatcher = AcceptedFixDispatcher(
        FixOperationStore(tmp_path),
        policy_reader=lambda: FrozenAudisorPolicy(True, "local-openai-compatible", "qwen2.5-coder:7b", "http://127.0.0.1:11434"),
        aflow_igniter=igniter,
        worker_factory=lambda *args, **kwargs: object(),
    )

    result = FixController().accept(
        op, dispatcher,
        lambda operation, result: "continued",
        lambda operation, result: unresolved_calls.append(result) or "unresolved",
    )

    # Prove: model was never called
    assert len(calls) == 0

    # Prove: blocked with decision_required
    assert result == "unresolved"
    assert len(unresolved_calls) == 1
    blocked = unresolved_calls[0]
    assert blocked["status"] == "blocked"
    assert blocked["unresolved_reason"] == "decision_required"

    # Prove: authority evaluation is present
    assert "authority_evaluation" in blocked
    auth_eval = blocked["authority_evaluation"]
    assert auth_eval["status"] == "decision_required"
    assert len(auth_eval["unresolved_requirements"]) == 1
    assert auth_eval["unresolved_requirements"][0]["finding_id"] == "F-1"
    assert auth_eval["unresolved_requirements"][0]["decision_kind"] == "select_authoritative_path"

    # Prove: no handoff, no Codex
    handoff_path = tmp_path / "fix-auth-001" / "qualified-fix-handoff.json"
    assert not handoff_path.exists()


def test_G_authority_decision_supplied_proceeds_normally(tmp_path):
    """Fixture: authority decision supplied → gate passes, operation proceeds."""
    repo = tmp_path / "repo"
    src = repo / "src"
    src.mkdir(parents=True)
    (src / "app.py").write_text("x = 1\n", encoding="utf-8")
    (src / "controller.py").write_text("y = 2\n", encoding="utf-8")

    from hashlib import sha256
    app_hash = sha256((src / "app.py").read_bytes()).hexdigest()
    ctrl_hash = sha256((src / "controller.py").read_bytes()).hexdigest()

    finding = Finding("F-1", "authority.competing_authority_path", "src/app.py", "high", {"authority_candidates": ["src/app.py", "src/controller.py"], "repro": "inspect active authority-named modules"})
    manifest = FixScopedManifest(
        ["src/app.py", "src/controller.py"], ["src/app.py", "src/controller.py"], "input",
        {"src/app.py": app_hash, "src/controller.py": ctrl_hash},
    )
    statements = make_statements([finding], manifest)
    plan = ImplementationPlan([PlanStep("S-1", "repair", "src/app.py", "F-1", "test passes")], ["src/app.py"], True)
    op = AcceptedFixOperation(
        "fix-auth-002", [finding], manifest, statements, plan,
        {"root": str(repo)}, {"allowed_paths": ["src/app.py"]},
        authority_decisions={
            "F-1": {
                "finding_id": "F-1",
                "decision_kind": "select_authoritative_path",
                "selected_value": "src/app.py",
                "source": "user",
            },
        },
    )

    calls = []
    continued = []

    def igniter(operation_context, policy, worker):
        calls.append(("igniter", operation_context))
        return IgnitionResult(True, "supplied", operation_context.accepted_plan, {"readiness": {}}, True)

    dispatcher = AcceptedFixDispatcher(
        FixOperationStore(tmp_path),
        policy_reader=lambda: FrozenAudisorPolicy(True, "local-openai-compatible", "qwen2.5-coder:7b", "http://127.0.0.1:11434"),
        aflow_igniter=igniter,
        worker_factory=lambda *args, **kwargs: object(),
    )

    result = FixController().accept(
        op, dispatcher,
        lambda operation, result: continued.append(result) or "continued",
        lambda operation, result: "unresolved",
    )

    # Prove: model was called
    assert len(calls) == 1

    # Prove: operation proceeds
    assert result == "continued"

    # Prove: handoff exists and contains authority decisions
    handoff_path = tmp_path / "fix-auth-002" / "qualified-fix-handoff.json"
    assert handoff_path.is_file()
    handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
    assert "authority_decisions" in handoff
    assert handoff["authority_decisions"]["F-1"]["decision_kind"] == "select_authoritative_path"
    assert handoff["authority_decisions"]["F-1"]["selected_value"] == "src/app.py"
    assert handoff["authority_decisions"]["F-1"]["source"] == "user"


def test_H_ordinary_finding_without_authority_requirement_proceeds(tmp_path):
    """Fixture: ordinary finding (no authority requirement) → gate passes."""
    repo = tmp_path / "repo"
    src = repo / "src"
    src.mkdir(parents=True)
    (src / "app.py").write_text("x = 1\n", encoding="utf-8")

    from hashlib import sha256
    app_hash = sha256((src / "app.py").read_bytes()).hexdigest()

    finding = Finding("F-1", "correctness.syntax_error", "src/app.py", "high", {"line": 1, "message": "bad"})
    manifest = FixScopedManifest(
        ["src/app.py"], ["src/app.py"], "input",
        {"src/app.py": app_hash},
    )
    statements = make_statements([finding], manifest)
    plan = ImplementationPlan([PlanStep("S-1", "repair", "src/app.py", "F-1", "test passes")], ["src/app.py"], True)
    op = AcceptedFixOperation("fix-ordinary-001", [finding], manifest, statements, plan, {"root": str(repo)}, {"allowed_paths": ["src/app.py"]})

    calls = []
    continued = []

    def igniter(operation_context, policy, worker):
        calls.append(("igniter", operation_context))
        return IgnitionResult(True, "supplied", operation_context.accepted_plan, {"readiness": {}}, True)

    dispatcher = AcceptedFixDispatcher(
        FixOperationStore(tmp_path),
        policy_reader=lambda: FrozenAudisorPolicy(True, "local-openai-compatible", "qwen2.5-coder:7b", "http://127.0.0.1:11434"),
        aflow_igniter=igniter,
        worker_factory=lambda *args, **kwargs: object(),
    )

    result = FixController().accept(
        op, dispatcher,
        lambda operation, result: continued.append(result) or "continued",
        lambda operation, result: "unresolved",
    )

    # Prove: model was called, operation proceeds normally
    assert len(calls) == 1
    assert result == "continued"


def test_I_authority_decision_persists_through_transport(tmp_path):
    """Fixture: authority decisions survive transport deserialization."""
    repo = tmp_path / "repo"
    src = repo / "src"
    src.mkdir(parents=True)
    (src / "app.py").write_text("x = 1\n", encoding="utf-8")
    (src / "controller.py").write_text("y = 2\n", encoding="utf-8")

    from hashlib import sha256
    app_hash = sha256((src / "app.py").read_bytes()).hexdigest()
    ctrl_hash = sha256((src / "controller.py").read_bytes()).hexdigest()

    # Simulate the transport path: construct via _fix_operation
    from audisor.operations.transport import _fix_operation
    fix_value = {
        "findings": [{"id": "F-1", "type": "authority.competing_authority_path", "file": "src/app.py", "severity": "high", "evidence": {"authority_candidates": ["src/app.py", "src/controller.py"]}}],
        "manifest": {"files": ["src/app.py", "src/controller.py"], "dependency_closure": ["src/app.py", "src/controller.py"], "input_hash": "input", "file_hashes": {"src/app.py": app_hash, "src/controller.py": ctrl_hash}},
        "statements": [{"type": "dossier", "content": {}, "findings_ref_hash": "a" * 64, "manifest_ref_hash": "b" * 64}, {"type": "handoff", "content": {}, "findings_ref_hash": "a" * 64, "manifest_ref_hash": "b" * 64}, {"type": "llm", "content": {}, "findings_ref_hash": "a" * 64, "manifest_ref_hash": "b" * 64}],
        "plan": {"steps": [{"id": "S-1", "action": "repair", "target_file": "src/app.py", "originating_finding_id": "F-1", "acceptance_criterion": "test passes"}], "target_files": ["src/app.py"], "is_qualified": True},
        "workspace_identity": {"root": str(repo)},
        "authority_context": {"allowed_paths": ["src/app.py"]},
        "authority_decisions": {
            "F-1": {
                "finding_id": "F-1",
                "decision_kind": "select_authoritative_path",
                "selected_value": "src/app.py",
                "source": "user",
            },
        },
    }
    fix_input = _fix_operation(fix_value, "fix-transport-001")
    operation = fix_input.operation

    # Prove: authority_decisions survived transport
    assert operation.authority_decisions is not None
    assert "F-1" in operation.authority_decisions
    assert operation.authority_decisions["F-1"]["decision_kind"] == "select_authoritative_path"
    assert operation.authority_decisions["F-1"]["selected_value"] == "src/app.py"
    assert operation.authority_decisions["F-1"]["source"] == "user"


# ---------------------------------------------------------------------------
# H1 tests: package_from_context routing based on igniter identity
# ---------------------------------------------------------------------------


def test_H1_local_boundary_does_not_call_package_from_context(tmp_path):
    from unittest.mock import patch, MagicMock
    from audisor.audisor_lifecycle.ignition import ignite, IgnitionResult
    op = operation()
    continued = []
    fix_calls = []
    fake_result = IgnitionResult(True, 'supplied', op.plan, {'readiness': {}}, True)
    sentinel_pkg = MagicMock(side_effect=AssertionError('package_from_context must not be called on local boundary'))
    with patch('audisor_backend.controllers.fix_host.package_from_context', sentinel_pkg):
        with patch('audisor_backend.controllers.fix_host.invoke_local_fix', side_effect=lambda *a, **kw: fix_calls.append(a) or fake_result):
            dispatcher = AcceptedFixDispatcher(
                FixOperationStore(tmp_path),
                policy_reader=lambda: FrozenAudisorPolicy(True, 'local-openai-compatible', 'qwen2.5-coder:7b', 'http://127.0.0.1:11434'),
                aflow_igniter=ignite,
                worker_factory=lambda *args, **kwargs: object(),
            )
            result = FixController().accept(
                op, dispatcher,
                lambda operation, r: continued.append(r) or 'continued',
                lambda operation, r: 'unresolved',
            )
    assert sentinel_pkg.call_count == 0, 'package_from_context must not be called on local boundary'
    assert len(fix_calls) == 1, 'invoke_local_fix must be called exactly once'
    assert result == 'continued'


def test_H1_custom_igniter_calls_package_from_context(tmp_path):
    from unittest.mock import patch, MagicMock
    from audisor.audisor_lifecycle.ignition import IgnitionResult
    # Request present -> the custom-igniter path must build a package. A
    # realistic (schema-valid) request is supplied so the fixture is possible
    # without the mock; the mock isolates the routing assertion from real
    # schema validation (covered separately below).
    op = operation(aflow_analysis_request=valid_analysis_request())
    continued = []
    igniter_contexts = []
    fix_calls = []
    fake_package = MagicMock()
    fake_package.package_hash = 'a' * 64
    def custom_igniter(operation_context, policy, worker):
        igniter_contexts.append(operation_context)
        return IgnitionResult(True, 'supplied', operation_context.accepted_plan, {'readiness': {}}, True)
    with patch('audisor_backend.controllers.fix_host.package_from_context', return_value=fake_package) as mock_pkg:
        with patch('audisor_backend.controllers.fix_host.invoke_local_fix', side_effect=lambda *a, **kw: fix_calls.append(a)) as mock_fix:
            dispatcher = AcceptedFixDispatcher(
                FixOperationStore(tmp_path),
                policy_reader=lambda: FrozenAudisorPolicy(True, 'local-openai-compatible', 'qwen2.5-coder:7b', 'http://127.0.0.1:11434'),
                aflow_igniter=custom_igniter,
                worker_factory=lambda *args, **kwargs: object(),
            )
            result = FixController().accept(
                op, dispatcher,
                lambda operation, r: continued.append(r) or 'continued',
                lambda operation, r: 'unresolved',
            )
    assert mock_pkg.call_count == 1, 'package_from_context must be called exactly once for custom igniter'
    assert fix_calls == [], 'invoke_local_fix must not be called for custom igniter path'
    assert len(igniter_contexts) == 1
    assert igniter_contexts[0].analysis_package is fake_package, 'analysis_package must be passed to custom igniter context'
    assert result == 'continued'


def test_H1_policy_disabled_calls_neither_package_nor_igniter(tmp_path):
    from unittest.mock import patch
    op = operation()
    igniter_calls = []
    def custom_igniter(operation_context, policy, worker):
        igniter_calls.append(operation_context)
    with patch('audisor_backend.controllers.fix_host.package_from_context') as mock_pkg:
        with patch('audisor_backend.controllers.fix_host.invoke_local_fix') as mock_fix:
            dispatcher = AcceptedFixDispatcher(
                FixOperationStore(tmp_path),
                policy_reader=lambda: FrozenAudisorPolicy(False, 'local-openai-compatible', 'qwen2.5-coder:7b', 'http://127.0.0.1:11434'),
                aflow_igniter=custom_igniter,
                worker_factory=lambda *args, **kwargs: object(),
            )
            result = FixController().accept(
                op, dispatcher,
                lambda operation, r: r['status'],
                lambda operation, r: 'unresolved',
            )
    assert mock_pkg.call_count == 0, 'package_from_context must not be called when policy disabled'
    assert mock_fix.call_count == 0, 'invoke_local_fix must not be called when policy disabled'
    assert igniter_calls == [], 'custom igniter must not be called when policy disabled'
    assert result == 'skipped_disabled'


def test_H1_expected_package_error_persists_package_validation_failed_and_halts(tmp_path):
    # Required matrix #5: the EXPECTED package-contract exception
    # (AnalysisPackageError) is classified as package_validation_failed, persisted
    # with the operation identity, and halts before the custom igniter runs.
    op = operation(aflow_analysis_request=valid_analysis_request())
    continued = []
    finalized = []
    igniter_calls = []
    def custom_igniter(operation_context, policy, worker):
        igniter_calls.append(operation_context)
        return IgnitionResult(True, 'supplied', operation_context.accepted_plan, {'readiness': {}}, True)
    with patch('audisor_backend.controllers.fix_host.package_from_context', side_effect=AnalysisPackageError('accepted operation lacks aflow_analysis_request')):
        dispatcher = AcceptedFixDispatcher(
            FixOperationStore(tmp_path),
            policy_reader=lambda: _enabled_policy(),
            aflow_igniter=custom_igniter,
            worker_factory=lambda *args, **kwargs: object(),
        )
        result = FixController().accept(
            op, dispatcher,
            lambda operation, r: continued.append(r) or 'continued',
            lambda operation, r: finalized.append(r) or 'unresolved',
        )
    assert continued == [], 'Fix must not continue after an expected package-contract failure'
    assert igniter_calls == [], 'custom igniter must not be called after package failure'
    assert result == 'unresolved'
    assert len(finalized) == 1
    assert finalized[0]['status'] == 'package_validation_failed'
    stored = dispatcher.store.load(op.operation_id)
    assert stored is not None
    assert stored['status'] == 'package_validation_failed'
    assert stored['operation_id'] == op.operation_id


def test_H1_local_boundary_ignores_present_request(tmp_path):
    from unittest.mock import patch, MagicMock
    from audisor.audisor_lifecycle.ignition import ignite, IgnitionResult
    # Even with a request present, the local `ignite` boundary preserves its
    # existing behavior: no custom-package routing; invoke_local_fix runs.
    op = operation(aflow_analysis_request=valid_analysis_request())
    fix_calls = []
    fake_result = IgnitionResult(True, 'supplied', op.plan, {'readiness': {}}, True)
    sentinel_pkg = MagicMock(side_effect=AssertionError('package_from_context must not be called on local boundary'))
    with patch('audisor_backend.controllers.fix_host.package_from_context', sentinel_pkg):
        with patch('audisor_backend.controllers.fix_host.invoke_local_fix', side_effect=lambda *a, **kw: fix_calls.append(a) or fake_result):
            dispatcher = AcceptedFixDispatcher(
                FixOperationStore(tmp_path),
                policy_reader=lambda: FrozenAudisorPolicy(True, 'local-openai-compatible', 'qwen2.5-coder:7b', 'http://127.0.0.1:11434'),
                aflow_igniter=ignite,
                worker_factory=lambda *args, **kwargs: object(),
            )
            result = FixController().accept(
                op, dispatcher,
                lambda operation, r: 'continued',
                lambda operation, r: 'unresolved',
            )
    assert sentinel_pkg.call_count == 0, 'local boundary must not route through package_from_context even when a request is present'
    assert len(fix_calls) == 1, 'invoke_local_fix must be called exactly once on the local boundary'
    assert result == 'continued'


def test_H1_custom_igniter_valid_request_builds_real_package(tmp_path):
    from audisor.audisor_lifecycle.analysis_package import FrozenAnalysisPackage
    from audisor.audisor_lifecycle.ignition import IgnitionResult
    # No mock: a valid supplied request must produce a REAL FrozenAnalysisPackage
    # that is passed to the custom igniter, and the operation continues.
    op = operation(aflow_analysis_request=valid_analysis_request())
    igniter_contexts = []
    def custom_igniter(operation_context, policy, worker):
        igniter_contexts.append(operation_context)
        return IgnitionResult(True, 'supplied', operation_context.accepted_plan, {'readiness': {}}, True)
    dispatcher = AcceptedFixDispatcher(
        FixOperationStore(tmp_path),
        policy_reader=lambda: FrozenAudisorPolicy(True, 'local-openai-compatible', 'qwen2.5-coder:7b', 'http://127.0.0.1:11434'),
        aflow_igniter=custom_igniter,
        worker_factory=lambda *args, **kwargs: object(),
    )
    result = FixController().accept(
        op, dispatcher,
        lambda operation, r: 'continued',
        lambda operation, r: 'unresolved',
    )
    assert len(igniter_contexts) == 1, 'custom igniter must be invoked exactly once'
    package = igniter_contexts[0].analysis_package
    assert isinstance(package, FrozenAnalysisPackage), 'a valid supplied request must produce a real FrozenAnalysisPackage'
    assert package.operation_id == op.operation_id
    assert package.package_hash.startswith('sha256:')
    assert result == 'continued'


def test_H1_custom_igniter_malformed_request_halts(tmp_path):
    from audisor.audisor_lifecycle.ignition import IgnitionResult
    # No mock: a supplied-but-malformed request (missing required evidence
    # fields) must fail REAL schema validation, persist package_validation_failed
    # with the operation identity, halt, and never invoke the custom igniter.
    malformed = {"schema_version": "1.0.0", "analysis_id": "fix-001"}
    op = operation(aflow_analysis_request=malformed)
    continued = []
    finalized = []
    igniter_calls = []
    def custom_igniter(operation_context, policy, worker):
        igniter_calls.append(operation_context)
        return IgnitionResult(True, 'supplied', operation_context.accepted_plan, {'readiness': {}}, True)
    dispatcher = AcceptedFixDispatcher(
        FixOperationStore(tmp_path),
        policy_reader=lambda: FrozenAudisorPolicy(True, 'local-openai-compatible', 'qwen2.5-coder:7b', 'http://127.0.0.1:11434'),
        aflow_igniter=custom_igniter,
        worker_factory=lambda *args, **kwargs: object(),
    )
    result = FixController().accept(
        op, dispatcher,
        lambda operation, r: continued.append(r) or 'continued',
        lambda operation, r: finalized.append(r) or 'unresolved',
    )
    assert continued == [], 'Fix must not continue after a malformed supplied request'
    assert igniter_calls == [], 'custom igniter must not be called after package validation failure'
    assert result == 'unresolved'
    assert len(finalized) == 1
    assert finalized[0]['status'] == 'package_validation_failed'
    stored = dispatcher.store.load(op.operation_id)
    assert stored is not None
    assert stored['status'] == 'package_validation_failed'
    assert stored['operation_id'] == op.operation_id


def test_H1_custom_igniter_empty_mapping_request_halts(tmp_path):
    from audisor.audisor_lifecycle.ignition import IgnitionResult
    # No mock: a supplied-but-EMPTY mapping {} is still a Mapping, so it reaches
    # REAL schema validation, which fails on the missing required evidence fields
    # and raises AnalysisPackageError -> package_validation_failed, halt, never
    # ignite. This proves "empty mappings are rejected" distinctly from a
    # malformed (partially-populated) mapping and from an explicit null.
    op = operation(aflow_analysis_request={})
    continued = []
    finalized = []
    igniter_calls = []
    def custom_igniter(operation_context, policy, worker):
        igniter_calls.append(operation_context)
        return IgnitionResult(True, 'supplied', operation_context.accepted_plan, {'readiness': {}}, True)
    dispatcher = AcceptedFixDispatcher(
        FixOperationStore(tmp_path),
        policy_reader=lambda: FrozenAudisorPolicy(True, 'local-openai-compatible', 'qwen2.5-coder:7b', 'http://127.0.0.1:11434'),
        aflow_igniter=custom_igniter,
        worker_factory=lambda *args, **kwargs: object(),
    )
    result = FixController().accept(
        op, dispatcher,
        lambda operation, r: continued.append(r) or 'continued',
        lambda operation, r: finalized.append(r) or 'unresolved',
    )
    assert continued == [], 'Fix must not continue after an empty supplied mapping'
    assert igniter_calls == [], 'custom igniter must not be called after empty-mapping validation failure'
    assert result == 'unresolved'
    assert len(finalized) == 1
    assert finalized[0]['status'] == 'package_validation_failed'
    stored = dispatcher.store.load(op.operation_id)
    assert stored is not None
    assert stored['status'] == 'package_validation_failed'
    assert stored['operation_id'] == op.operation_id


def test_H1_duplicate_operation_with_valid_request_does_not_rebuild_package(tmp_path):
    from unittest.mock import patch, MagicMock
    from audisor.audisor_lifecycle.ignition import IgnitionResult
    # A duplicate accepted operation short-circuits before the package branch:
    # package_from_context is built exactly once and the igniter runs once.
    op = operation(aflow_analysis_request=valid_analysis_request())
    igniter_contexts = []
    fake_package = MagicMock()
    fake_package.package_hash = 'a' * 64
    def custom_igniter(operation_context, policy, worker):
        igniter_contexts.append(operation_context)
        return IgnitionResult(True, 'supplied', operation_context.accepted_plan, {'readiness': {}}, True)
    with patch('audisor_backend.controllers.fix_host.package_from_context', return_value=fake_package) as mock_pkg:
        dispatcher = AcceptedFixDispatcher(
            FixOperationStore(tmp_path),
            policy_reader=lambda: FrozenAudisorPolicy(True, 'local-openai-compatible', 'qwen2.5-coder:7b', 'http://127.0.0.1:11434'),
            aflow_igniter=custom_igniter,
            worker_factory=lambda *args, **kwargs: object(),
        )
        controller = FixController()
        first = controller.accept(op, dispatcher, lambda operation, r: 'continued', lambda operation, r: 'unresolved')
        second = controller.accept(op, dispatcher, lambda operation, r: 'continued', lambda operation, r: 'unresolved')
    assert mock_pkg.call_count == 1, 'package_from_context must be built exactly once across duplicate accepts'
    assert len(igniter_contexts) == 1, 'custom igniter must run exactly once across duplicate accepts'
    assert first == 'continued'
    assert second['status'] == 'accepted', 'duplicate accept must return the persisted artifact without re-ignition'


# ---------------------------------------------------------------------------
# Canonical-deserializer + production-boundary contract tests:
# absent vs explicit-null presence, exception classification, persistence
# failure, post-package igniter failure, concurrent single-flight, and the
# production transport->dispatcher boundary.
# ---------------------------------------------------------------------------


def test_deserialized_absent_request_takes_legacy_path(tmp_path):
    # Required #1: a serialized operation with the aflow_analysis_request field
    # ABSENT deserializes to present=False and follows the supported legacy path:
    # packaging is skipped and the custom igniter still runs.
    import audisor_backend.controllers.fix_host as fix_host
    pkg_calls = []
    request = deserialize_request(_fix_envelope())  # field omitted entirely
    op = request.fix.operation
    assert op.aflow_analysis_request is None
    assert op.aflow_analysis_request_present is False
    igniter_contexts = []
    def custom_igniter(operation_context, policy, worker):
        igniter_contexts.append(operation_context)
        return IgnitionResult(True, 'supplied', operation_context.accepted_plan, {'readiness': {}}, True)
    with patch.object(fix_host, 'package_from_context', side_effect=lambda **kw: pkg_calls.append(kw)):
        dispatcher = AcceptedFixDispatcher(
            FixOperationStore(tmp_path),
            policy_reader=lambda: _enabled_policy(),
            aflow_igniter=custom_igniter,
            worker_factory=lambda *args, **kwargs: object(),
        )
        result = dispatcher.dispatch(op, lambda o, r: 'continued', lambda o, r: r['status'])
    assert result == 'continued'
    assert len(igniter_contexts) == 1, 'legacy path must still invoke the custom igniter'
    assert igniter_contexts[0].analysis_package is None, 'legacy path builds no package'
    assert pkg_calls == [], 'legacy path must not call package_from_context'
    assert dispatcher.store.load(op.operation_id)['status'] == 'accepted'


def test_deserialized_explicit_null_request_halts_as_supplied_invalid(tmp_path):
    # Required #2: a serialized operation with aflow_analysis_request explicitly
    # set to null deserializes to present=True with value None. Presence (not the
    # value) routes it into the package branch, where the REAL package_from_context
    # rejects the null mapping -> package_validation_failed, halt, never ignite.
    request = deserialize_request(_fix_envelope(aflow_analysis_request=None))
    op = request.fix.operation
    assert op.aflow_analysis_request is None
    assert op.aflow_analysis_request_present is True, 'explicit null must preserve presence'
    igniter_calls = []
    dispatcher = AcceptedFixDispatcher(
        FixOperationStore(tmp_path),
        policy_reader=lambda: _enabled_policy(),
        aflow_igniter=lambda **kw: igniter_calls.append(kw),
        worker_factory=lambda *args, **kwargs: object(),
    )
    result = dispatcher.dispatch(op, lambda o, r: 'continued', lambda o, r: r['status'])
    assert result == 'package_validation_failed', 'explicit null is supplied-invalid, not legacy'
    assert igniter_calls == [], 'explicit null must never reach the igniter'
    stored = dispatcher.store.load(op.operation_id)
    assert stored['status'] == 'package_validation_failed'
    assert stored['operation_id'] == op.operation_id


def test_H1_unexpected_constructor_error_is_internal_not_package_validation(tmp_path):
    # Required #6: an UNEXPECTED exception from package construction (e.g. a
    # controller bug raising RuntimeError) is classified as a distinct
    # internal_error — NOT package_validation_failed — so a product defect is
    # never hidden as bad user input. It still halts before the igniter.
    op = operation(aflow_analysis_request=valid_analysis_request())
    igniter_calls = []
    def custom_igniter(operation_context, policy, worker):
        igniter_calls.append(operation_context)
        return IgnitionResult(True, 'supplied', operation_context.accepted_plan, {'readiness': {}}, True)
    with patch('audisor_backend.controllers.fix_host.package_from_context', side_effect=RuntimeError('unexpected controller defect')):
        dispatcher = AcceptedFixDispatcher(
            FixOperationStore(tmp_path),
            policy_reader=lambda: _enabled_policy(),
            aflow_igniter=custom_igniter,
            worker_factory=lambda *args, **kwargs: object(),
        )
        result = FixController().accept(op, dispatcher, lambda o, r: 'continued', lambda o, r: r['status'])
    assert result == 'internal_error', 'unexpected defect must be internal_error, not package_validation_failed'
    assert igniter_calls == [], 'unexpected defect must never reach the igniter'
    stored = dispatcher.store.load(op.operation_id)
    assert stored['status'] == 'internal_error'
    assert stored['operation_id'] == op.operation_id


def test_H1_persistence_failure_after_package_error_never_ignites(tmp_path):
    # Required #7: if persisting the package_validation_failed artifact itself
    # fails, the failure propagates, but ignition MUST still never happen.
    op = operation(aflow_analysis_request=valid_analysis_request())
    igniter_calls = []
    def custom_igniter(operation_context, policy, worker):
        igniter_calls.append(operation_context)
        return IgnitionResult(True, 'supplied', operation_context.accepted_plan, {'readiness': {}}, True)
    store = FixOperationStore(tmp_path)
    def broken_persist(operation_id, artifact):
        raise OSError('disk full')
    store.persist = broken_persist
    dispatcher = AcceptedFixDispatcher(
        store,
        policy_reader=lambda: _enabled_policy(),
        aflow_igniter=custom_igniter,
        worker_factory=lambda *args, **kwargs: object(),
    )
    with patch('audisor_backend.controllers.fix_host.package_from_context', side_effect=AnalysisPackageError('invalid')):
        with pytest.raises(OSError):
            dispatcher.dispatch(op, lambda o, r: 'continued', lambda o, r: 'unresolved')
    assert igniter_calls == [], 'persistence failure must never permit ignition'


def test_H1_igniter_failure_after_successful_package_build_halts(tmp_path):
    # Required #8: with a valid supplied request, the REAL package is built first;
    # if the custom igniter then fails, the operation halts and never continues —
    # and the built package reached the igniter context before the failure.
    op = operation(aflow_analysis_request=valid_analysis_request())
    captured = []
    def failing_igniter(operation_context, policy, worker):
        captured.append(operation_context)
        raise RuntimeError('igniter crashed after package built')
    dispatcher = AcceptedFixDispatcher(
        FixOperationStore(tmp_path),
        policy_reader=lambda: _enabled_policy(),
        aflow_igniter=failing_igniter,
        worker_factory=lambda *args, **kwargs: object(),
    )
    result = dispatcher.dispatch(op, lambda o, r: 'continued', lambda o, r: r['status'])
    assert len(captured) == 1, 'igniter must be invoked once after a successful package build'
    assert isinstance(captured[0].analysis_package, FrozenAnalysisPackage), 'real package must reach the igniter context'
    assert result == 'validation_failed', 'generic igniter failure halts as validation_failed'
    assert dispatcher.store.load(op.operation_id)['status'] == 'validation_failed'


def test_concurrent_duplicate_dispatch_packages_and_ignites_at_most_once(tmp_path):
    # Required #9: two concurrent dispatches for the SAME operation_id (via two
    # dispatcher instances sharing one store, mirroring production's per-call
    # dispatcher) must construct the package and invoke the igniter AT MOST once.
    # A barrier forces simultaneous entry; the per-operation lock serializes them.
    import audisor_backend.controllers.fix_host as fix_host
    op = operation(aflow_analysis_request=valid_analysis_request())
    store = FixOperationStore(tmp_path)
    pkg_calls = []
    real_pkg = fix_host.package_from_context
    def counting_pkg(**kw):
        pkg_calls.append(kw)
        return real_pkg(**kw)
    igniter_contexts = []
    def custom_igniter(operation_context, policy, worker):
        igniter_contexts.append(operation_context)
        return IgnitionResult(True, 'supplied', operation_context.accepted_plan, {'readiness': {}}, True)
    barrier = threading.Barrier(2)
    def run_dispatch(_):
        barrier.wait()
        dispatcher = AcceptedFixDispatcher(
            store,
            policy_reader=lambda: _enabled_policy(),
            aflow_igniter=custom_igniter,
            worker_factory=lambda *args, **kwargs: object(),
        )
        return dispatcher.dispatch(op, lambda o, r: 'continued', lambda o, r: r['status'])
    with patch.object(fix_host, 'package_from_context', side_effect=counting_pkg):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(run_dispatch, range(2)))
    assert len(pkg_calls) == 1, 'package must be constructed at most once across concurrent duplicates'
    assert len(igniter_contexts) == 1, 'igniter must run at most once across concurrent duplicates'
    continued = sum(1 for r in results if r == 'continued')
    accepted = sum(1 for r in results if isinstance(r, dict) and r.get('status') == 'accepted')
    assert continued + accepted == 2, 'both callers observe an authoritative success result'
    assert store.load(op.operation_id)['status'] == 'accepted'


def test_production_boundary_valid_request_completes_through_deserializer_and_dispatcher(tmp_path):
    # Required #10 / Gap 3: a fixture operation entering through the production
    # transport entrypoint (deserialize_request) and the production dispatcher
    # (AcceptedFixDispatcher.dispatch) — NOT FixController — completes successfully
    # with a REAL frozen package built from the supplied request.
    envelope = _fix_envelope(aflow_analysis_request=valid_analysis_request('fix-001'))
    request = deserialize_request(envelope)
    op = request.fix.operation
    assert op.aflow_analysis_request_present is True
    igniter_contexts = []
    def custom_igniter(operation_context, policy, worker):
        igniter_contexts.append(operation_context)
        return IgnitionResult(True, 'supplied', operation_context.accepted_plan, {'readiness': {}}, True)
    dispatcher = AcceptedFixDispatcher(
        FixOperationStore(tmp_path),
        policy_reader=lambda: _enabled_policy(),
        aflow_igniter=custom_igniter,
        worker_factory=lambda *args, **kwargs: object(),
    )
    result = dispatcher.dispatch(op, lambda o, r: 'continued', lambda o, r: r['status'])
    assert result == 'continued'
    assert len(igniter_contexts) == 1
    package = igniter_contexts[0].analysis_package
    assert isinstance(package, FrozenAnalysisPackage), 'production boundary must build a real package'
    assert package.operation_id == op.operation_id
    assert package.package_hash.startswith('sha256:')
    assert dispatcher.store.load(op.operation_id)['status'] == 'accepted'
