"""Tests for the public review_contract lifecycle wrapper."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from audisor.audisor_lifecycle.active_state import read_active_state
from audisor.audisor_lifecycle.adapter import verify_contract
from audisor.audisor_lifecycle.contract import AudisorLifecycleError, verify_lock
from audisor.audisor_lifecycle.hook import evaluate_hook_payload, verify_active_state
from audisor.audisor_lifecycle.review_contract import (
    build_analysis_for_lock,
    map_decision_to_frozen,
    review_and_lock,
)
from audisor.operations.artifacts import ArtifactStore
from audisor.operations.store import AudisorOperationStore

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "aflow_contract"


def ready_input() -> dict:
    return json.loads((FIXTURES / "ready-input.json").read_text(encoding="utf-8"))


def clean_analysis_request() -> dict:
    """Build a complete analysis request that passes aflow.analyze() cleanly."""
    from aflow.fixtures.factory import request_bundle
    return request_bundle()


def contract_inputs() -> dict:
    """Extract contract assembly inputs from the ready-input fixture."""
    data = ready_input()
    return {
        "accepted_task_input": data["accepted_task_input"],
        "candidate_implementation_plan": data["candidate_implementation_plan"],
        "authority": data["authority"],
        "baseline_evidence": data["baseline_evidence"],
        "accepted_constraints": data["accepted_constraints"],
        "required_outputs": data["required_outputs"],
    }


class TestMapDecisionToFrozen:
    """Tests for map_decision_to_frozen."""

    def test_clean_decision(self) -> None:
        decision = {"decision": "no_material_gap", "findings": []}
        frozen = map_decision_to_frozen(decision)
        assert frozen["decision"] == "no_material_gap"
        assert frozen["unresolved_items"] == []

    def test_blocking_decision(self) -> None:
        findings = [{"finding_id": "f1", "gap_type": "structural_missing"}]
        decision = {"decision": "material_gap_found", "findings": findings}
        frozen = map_decision_to_frozen(decision)
        assert frozen["decision"] == "material_gap_found"
        assert frozen["unresolved_items"] == findings


class TestBuildAnalysisForLock:
    """Tests for build_analysis_for_lock."""

    def test_produces_valid_lock_structure(self) -> None:
        inputs = contract_inputs()
        plan = inputs["candidate_implementation_plan"]
        task = inputs["accepted_task_input"]
        result = build_analysis_for_lock(plan, task, "a" * 64)
        assert result["decision"]["aflow_decision"] == "no_material_gap"
        assert result["decision"]["plan_ready_for_primary_decision"] is True
        assert result["plan_gaps"] == []
        payload = result["lock_payload"]
        assert payload["hash_algorithm"] == "sha256"
        assert all(
            isinstance(payload[key], str)
            for key in payload
            if key != "hash_algorithm"
        )


class TestReviewAndLock:
    """Tests for the full review_and_lock pipeline."""

    def test_clean_review_creates_lock_and_state(self, tmp_path: Path) -> None:
        """A clean analysis request should produce a valid lock and state."""
        inputs = contract_inputs()
        result = review_and_lock(
            analysis_request=clean_analysis_request(),
            accepted_task_input=inputs["accepted_task_input"],
            candidate_implementation_plan=inputs["candidate_implementation_plan"],
            authority=inputs["authority"],
            baseline_evidence=inputs["baseline_evidence"],
            accepted_constraints=inputs["accepted_constraints"],
            required_outputs=inputs["required_outputs"],
            operation_id="op.clean-1",
            state_root=tmp_path,
        )
        assert result["status"] == "ok"
        assert result["decision"] == "no_material_gap"
        assert result["blocking"] is False
        assert result["execution_ready"] is True
        assert result["findings"] == []
        assert result["lock_state"]["present"] is True
        assert result["lock_state"]["valid"] is True
        assert result["contract_sha256"] is not None
        assert len(result["contract_sha256"]) == 64
        assert result["state_path"] is not None

    def test_state_envelope_passes_hook_verification(self, tmp_path: Path) -> None:
        """The written state must pass verify_active_state."""
        inputs = contract_inputs()
        review_and_lock(
            analysis_request=clean_analysis_request(),
            accepted_task_input=inputs["accepted_task_input"],
            candidate_implementation_plan=inputs["candidate_implementation_plan"],
            authority=inputs["authority"],
            baseline_evidence=inputs["baseline_evidence"],
            accepted_constraints=inputs["accepted_constraints"],
            required_outputs=inputs["required_outputs"],
            operation_id="op.hook-1",
            state_root=tmp_path,
        )
        state = read_active_state(tmp_path)
        assert state is not None
        valid, reason, contract = verify_active_state(state)
        assert valid, f"verify_active_state failed: {reason}"

    def test_blocking_review_does_not_create_state(self, tmp_path: Path) -> None:
        """A schema-invalid analysis request should block without state."""
        inputs = contract_inputs()
        # Missing required fields → schema admission fails → material_gap_found
        bad_request = {"schema_version": "1.0.0", "analysis_id": "analysis.bad"}
        result = review_and_lock(
            analysis_request=bad_request,
            accepted_task_input=inputs["accepted_task_input"],
            candidate_implementation_plan=inputs["candidate_implementation_plan"],
            authority=inputs["authority"],
            baseline_evidence=inputs["baseline_evidence"],
            accepted_constraints=inputs["accepted_constraints"],
            required_outputs=inputs["required_outputs"],
            operation_id="op.blocked-1",
            state_root=tmp_path,
        )
        assert result["status"] == "blocked"
        assert result["blocking"] is True
        assert result["lock_state"]["present"] is False
        assert read_active_state(tmp_path) is None

    def test_hook_allows_authorized_target_after_review(self, tmp_path: Path) -> None:
        """After a clean review, the hook should allow an authorized target."""
        inputs = contract_inputs()
        review_and_lock(
            analysis_request=clean_analysis_request(),
            accepted_task_input=inputs["accepted_task_input"],
            candidate_implementation_plan=inputs["candidate_implementation_plan"],
            authority=inputs["authority"],
            baseline_evidence=inputs["baseline_evidence"],
            accepted_constraints=inputs["accepted_constraints"],
            required_outputs=inputs["required_outputs"],
            operation_id="op.hook-allow",
            state_root=tmp_path,
        )
        # The ready-input fixture targets a specific path
        target = inputs["candidate_implementation_plan"]["implementation_plan"][0]["target_paths"][0]
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "apply_patch",
            "requested_targets": [target],
        }
        result = evaluate_hook_payload(payload, tmp_path)
        assert result["decision"] == "allow"
        assert result["exit_code"] == 0

    def test_hook_denies_unauthorized_target_after_review(self, tmp_path: Path) -> None:
        """After a clean review, the hook should deny an unauthorized target."""
        inputs = contract_inputs()
        review_and_lock(
            analysis_request=clean_analysis_request(),
            accepted_task_input=inputs["accepted_task_input"],
            candidate_implementation_plan=inputs["candidate_implementation_plan"],
            authority=inputs["authority"],
            baseline_evidence=inputs["baseline_evidence"],
            accepted_constraints=inputs["accepted_constraints"],
            required_outputs=inputs["required_outputs"],
            operation_id="op.hook-deny",
            state_root=tmp_path,
        )
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "apply_patch",
            "requested_targets": ["unauthorized/path.py"],
        }
        result = evaluate_hook_payload(payload, tmp_path)
        assert result["decision"] == "deny"
        assert result["exit_code"] == 1


class TestStorePersistence:
    """Tests proving operation and artifact store integration."""

    def test_clean_review_persists_operation_completed(self, tmp_path: Path) -> None:
        """A clean review persists a completed operation record."""
        inputs = contract_inputs()
        state_root = tmp_path / "state"
        op_store = AudisorOperationStore(tmp_path / "operations")
        art_store = ArtifactStore(tmp_path / "artifacts")
        result = review_and_lock(
            analysis_request=clean_analysis_request(),
            accepted_task_input=inputs["accepted_task_input"],
            candidate_implementation_plan=inputs["candidate_implementation_plan"],
            authority=inputs["authority"],
            baseline_evidence=inputs["baseline_evidence"],
            accepted_constraints=inputs["accepted_constraints"],
            required_outputs=inputs["required_outputs"],
            operation_id="op.store-clean",
            state_root=state_root,
            operation_store=op_store,
            artifact_store=art_store,
        )
        assert result["operation_status"] == "completed"
        # Verify operation store persistence
        op_state = op_store.get("op.store-clean")
        assert op_state is not None
        assert op_state.status == "completed"
        assert op_state.result_hash is not None
        assert len(op_state.artifacts) == 3  # contract, lock, decision

    def test_clean_review_persists_artifacts(self, tmp_path: Path) -> None:
        """A clean review persists contract, lock, and decision artifacts."""
        inputs = contract_inputs()
        state_root = tmp_path / "state"
        art_store = ArtifactStore(tmp_path / "artifacts")
        result = review_and_lock(
            analysis_request=clean_analysis_request(),
            accepted_task_input=inputs["accepted_task_input"],
            candidate_implementation_plan=inputs["candidate_implementation_plan"],
            authority=inputs["authority"],
            baseline_evidence=inputs["baseline_evidence"],
            accepted_constraints=inputs["accepted_constraints"],
            required_outputs=inputs["required_outputs"],
            operation_id="op.store-artifacts",
            state_root=state_root,
            artifact_store=art_store,
        )
        assert len(result["artifacts"]) == 3
        artifact_types = {a["artifact_type"] for a in result["artifacts"]}
        assert artifact_types == {"contract", "lock", "analysis"}
        # Verify artifacts are loadable from disk
        refs = art_store.list_operation_artifacts("op.store-artifacts")
        assert len(refs) == 3

    def test_blocking_review_persists_operation_blocked(self, tmp_path: Path) -> None:
        """A blocking review persists a blocked operation record."""
        inputs = contract_inputs()
        state_root = tmp_path / "state"
        op_store = AudisorOperationStore(tmp_path / "operations")
        art_store = ArtifactStore(tmp_path / "artifacts")
        bad_request = {"schema_version": "1.0.0", "analysis_id": "analysis.bad"}
        result = review_and_lock(
            analysis_request=bad_request,
            accepted_task_input=inputs["accepted_task_input"],
            candidate_implementation_plan=inputs["candidate_implementation_plan"],
            authority=inputs["authority"],
            baseline_evidence=inputs["baseline_evidence"],
            accepted_constraints=inputs["accepted_constraints"],
            required_outputs=inputs["required_outputs"],
            operation_id="op.store-blocked",
            state_root=state_root,
            operation_store=op_store,
            artifact_store=art_store,
        )
        assert result["status"] == "blocked"
        assert result["operation_status"] == "blocked"
        # Verify operation store persistence
        op_state = op_store.get("op.store-blocked")
        assert op_state is not None
        assert op_state.status == "blocked"
        assert op_state.error_code == "blocked"
        # Verify decision artifact persisted
        assert len(result["artifacts"]) == 1
        assert result["artifacts"][0]["artifact_type"] == "analysis"

    def test_idempotent_replay_returns_cached(self, tmp_path: Path) -> None:
        """Repeating the same request returns a cached result."""
        inputs = contract_inputs()
        state_root = tmp_path / "state"
        op_store = AudisorOperationStore(tmp_path / "operations")
        art_store = ArtifactStore(tmp_path / "artifacts")
        request = clean_analysis_request()
        kwargs = dict(
            analysis_request=request,
            accepted_task_input=inputs["accepted_task_input"],
            candidate_implementation_plan=inputs["candidate_implementation_plan"],
            authority=inputs["authority"],
            baseline_evidence=inputs["baseline_evidence"],
            accepted_constraints=inputs["accepted_constraints"],
            required_outputs=inputs["required_outputs"],
            operation_id="op.idempotent",
            state_root=state_root,
            operation_store=op_store,
            artifact_store=art_store,
        )
        first = review_and_lock(**kwargs)
        assert first["status"] == "ok"
        assert first.get("idempotent_replay") is not True
        # Second call with same request → idempotent replay
        second = review_and_lock(**kwargs)
        assert second["idempotent_replay"] is True
        assert second["operation_status"] == "completed"
        assert second["status"] == "ok"

    def test_conflict_rejects_different_request(self, tmp_path: Path) -> None:
        """Same operation_id with different request raises conflict."""
        inputs = contract_inputs()
        state_root = tmp_path / "state"
        op_store = AudisorOperationStore(tmp_path / "operations")
        art_store = ArtifactStore(tmp_path / "artifacts")
        kwargs = dict(
            analysis_request=clean_analysis_request(),
            accepted_task_input=inputs["accepted_task_input"],
            candidate_implementation_plan=inputs["candidate_implementation_plan"],
            authority=inputs["authority"],
            baseline_evidence=inputs["baseline_evidence"],
            accepted_constraints=inputs["accepted_constraints"],
            required_outputs=inputs["required_outputs"],
            operation_id="op.conflict",
            state_root=state_root,
            operation_store=op_store,
            artifact_store=art_store,
        )
        review_and_lock(**kwargs)
        # Different request with same operation_id
        different_inputs = contract_inputs()
        different_inputs["accepted_task_input"] = {"task": "different"}
        with pytest.raises(AudisorLifecycleError, match="different request"):
            review_and_lock(
                analysis_request=clean_analysis_request(),
                accepted_task_input=different_inputs["accepted_task_input"],
                candidate_implementation_plan=inputs["candidate_implementation_plan"],
                authority=inputs["authority"],
                baseline_evidence=inputs["baseline_evidence"],
                accepted_constraints=inputs["accepted_constraints"],
                required_outputs=inputs["required_outputs"],
                operation_id="op.conflict",
                state_root=state_root,
                operation_store=op_store,
                artifact_store=art_store,
            )
