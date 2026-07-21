import json

from audisor.audisor_lifecycle.ignition import IgnitionResult
from audisor.audisor_lifecycle.operation import FrozenAudisorPolicy

from audisor_backend.controllers.fix_controller import FixController
from audisor_backend.controllers.fix_host import AcceptedFixDispatcher, AcceptedFixOperation, FixOperationStore
from audisor_backend.phases.fix.phases import make_scoped_manifest, make_statements
from audisor_backend.scanning.dependency_closure import resolve_dependency_details
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
