from audisor.audisor_lifecycle.ignition import IgnitionResult
from audisor.audisor_lifecycle.operation import FrozenAudisorPolicy

from audisor_backend.controllers.fix_controller import FixController
from audisor_backend.controllers.fix_host import AcceptedFixDispatcher, AcceptedFixOperation, FixOperationStore
from audisor_backend.phases.fix.phases import make_scoped_manifest, make_statements
from audisor_backend.schemas.fix.models import Finding, FindingCheck, FixScopedManifest, ImplementationPlan, PlanStep, SuccessDefinition, ValidationSpec


def operation():
    findings = [Finding("F-1", "syntax", "src/app.py", "high", {"line": 1})]
    manifest = FixScopedManifest(["src/app.py"], ["src/app.py"], "input", {"src/app.py": "a" * 64})
    statements = make_statements(findings, manifest)
    plan = ImplementationPlan([PlanStep("S-1", "repair", "src/app.py", "F-1", "test passes")], ["src/app.py"], True)
    return AcceptedFixOperation("fix-001", findings, manifest, statements, plan, {"path": "sandbox/fix-001"}, {"allowed_paths": ["src/app.py"]})


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
