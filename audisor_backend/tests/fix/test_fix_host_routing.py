"""Routing-mode tests for FixDispatcher.

These tests verify the routing behavior (FixRoutingResult) when
AUDISOR_ROUTING_ENABLED=1. They are the routing-mode counterparts of the
hard_stop_behavior tests in test_fix_host.py.

Run with: AUDISOR_ROUTING_ENABLED=1 pytest -m routing_behavior tests/fix/test_fix_host_routing.py
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from audisor.audisor_lifecycle.analysis_package import AnalysisPackageError
from audisor.audisor_lifecycle.ignition import IgnitionResult
from audisor.audisor_lifecycle.operation import FrozenAudisorPolicy

from audisor_backend.controllers.fix_controller import FixController
from audisor_backend.controllers.fix_host import (
    AcceptedFixDispatcher,
    AcceptedFixOperation,
    FixOperationStore,
    FixRoutingResult,
)
from audisor_backend.schemas.fix.models import (
    Finding,
    FixScopedManifest,
    ImplementationPlan,
    PlanStep,
)
from audisor_backend.phases.fix.phases import make_scoped_manifest, make_statements


# ---------------------------------------------------------------------------
# Fixtures (shared structure with test_fix_host.py)
# ---------------------------------------------------------------------------

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
    import json
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


def _enabled_policy():
    return FrozenAudisorPolicy(True, "local-openai-compatible", "qwen2.5-coder:7b", "http://127.0.0.1:11434")


def operation(aflow_analysis_request=None, aflow_analysis_request_present=None):
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


# ---------------------------------------------------------------------------
# Routing behavior tests (AUDISOR_ROUTING_ENABLED=1)
# ---------------------------------------------------------------------------


@pytest.mark.routing_behavior
def test_invalid_fix_plan_returns_routing_result_instead_of_finalize(tmp_path):
    """In routing mode, invalid plan returns FixRoutingResult instead of calling finalize_unresolved."""
    op = operation()
    invalid = AcceptedFixOperation(
        op.operation_id, op.findings, op.manifest, op.statements,
        ImplementationPlan([], [], False),
        op.workspace_identity, op.authority_context,
    )
    calls = []

    with patch.dict(os.environ, {"AUDISOR_ROUTING_ENABLED": "1"}):
        dispatcher = AcceptedFixDispatcher(
            FixOperationStore(tmp_path),
            aflow_igniter=lambda **kwargs: calls.append(kwargs),
            routing_mode="routing",
        )
        result = FixController().accept(
            invalid, dispatcher,
            lambda operation, result: "continued",
            lambda operation, result: result,
        )

    # Routing mode returns the routing result directly instead of invoking
    # the legacy finalize_unresolved callback.
    assert calls == [], "igniter must not be called for invalid plan"
    assert isinstance(result, FixRoutingResult)
    assert result.route == "suspend_repair"
    assert result.suspend_reason == "needs_package_repair"
    assert result.artifact.get("status") == "validation_failed"


@pytest.mark.routing_behavior
def test_package_error_returns_routing_result_in_routing_mode(tmp_path):
    """In routing mode, package validation failure returns FixRoutingResult."""
    op = operation(aflow_analysis_request=valid_analysis_request())
    continued = []
    finalized = []
    igniter_calls = []

    def custom_igniter(operation_context, policy, worker):
        igniter_calls.append(operation_context)
        return IgnitionResult(True, "supplied", operation_context.accepted_plan, {"readiness": {}}, True)

    with patch.dict(os.environ, {"AUDISOR_ROUTING_ENABLED": "1"}):
        with patch(
            "audisor_backend.controllers.fix_host.package_from_context",
            side_effect=AnalysisPackageError("accepted operation lacks aflow_analysis_request"),
        ):
            dispatcher = AcceptedFixDispatcher(
                FixOperationStore(tmp_path),
                policy_reader=lambda: _enabled_policy(),
                aflow_igniter=custom_igniter,
                worker_factory=lambda *args, **kwargs: object(),
                routing_mode="routing",
            )
            result = FixController().accept(
                op, dispatcher,
                lambda operation, r: continued.append(r) or "continued",
                lambda operation, r: finalized.append(r) or "unresolved",
            )

    assert continued == [], "Fix must not continue after package-contract failure"
    assert igniter_calls == [], "custom igniter must not be called after package failure"
    # Routing mode returns the routing result directly; the legacy
    # finalize_unresolved callback is never invoked.
    assert finalized == []
    assert isinstance(result, FixRoutingResult)
    assert result.route == "suspend_repair"
    assert result.suspend_reason == "needs_package_repair"
    assert result.artifact.get("status") == "package_validation_failed"
    assert result.resume_input_schema is not None


@pytest.mark.routing_behavior
def test_malformed_request_returns_routing_result_in_routing_mode(tmp_path):
    """In routing mode, malformed analysis request returns FixRoutingResult."""
    malformed = {"schema_version": "1.0.0", "analysis_id": "fix-001"}
    op = operation(aflow_analysis_request=malformed)
    continued = []
    finalized = []
    igniter_calls = []

    def custom_igniter(operation_context, policy, worker):
        igniter_calls.append(operation_context)
        return IgnitionResult(True, "supplied", operation_context.accepted_plan, {"readiness": {}}, True)

    with patch.dict(os.environ, {"AUDISOR_ROUTING_ENABLED": "1"}):
        dispatcher = AcceptedFixDispatcher(
            FixOperationStore(tmp_path),
            policy_reader=lambda: _enabled_policy(),
            aflow_igniter=custom_igniter,
            worker_factory=lambda *args, **kwargs: object(),
            routing_mode="routing",
        )
        result = FixController().accept(
            op, dispatcher,
            lambda operation, r: continued.append(r) or "continued",
            lambda operation, r: finalized.append(r) or "unresolved",
        )

    assert continued == [], "Fix must not continue after malformed request"
    assert igniter_calls == [], "custom igniter must not be called after validation failure"
    # Routing mode returns the routing result directly; the legacy
    # finalize_unresolved callback is never invoked.
    assert finalized == []
    assert isinstance(result, FixRoutingResult)
    assert result.route == "suspend_repair"
    assert result.suspend_reason == "needs_package_repair"
    assert result.artifact.get("status") == "package_validation_failed"


@pytest.mark.routing_behavior
def test_routing_mode_legacy_preserves_original_behavior(tmp_path):
    """When routing_mode='legacy', original finalize_unresolved behavior is preserved."""
    op = operation()
    invalid = AcceptedFixOperation(
        op.operation_id, op.findings, op.manifest, op.statements,
        ImplementationPlan([], [], False),
        op.workspace_identity, op.authority_context,
    )

    dispatcher = AcceptedFixDispatcher(
        FixOperationStore(tmp_path),
        aflow_igniter=lambda **kwargs: None,
        routing_mode="legacy",
    )
    result = FixController().accept(
        invalid, dispatcher,
        lambda operation, result: "continued",
        lambda operation, result: result["status"],
    )
    # Legacy mode: plain dict with status
    assert result == "validation_failed"
