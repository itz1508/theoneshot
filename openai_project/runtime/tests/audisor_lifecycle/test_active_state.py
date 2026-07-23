"""Tests for the runtime-owned active-state envelope writer."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from audisor.audisor_lifecycle.active_state import (
    clear_active_state,
    read_active_state,
    write_active_state,
)
from audisor.audisor_lifecycle.adapter import assemble_contract
from audisor.audisor_lifecycle.contract import (
    AudisorLifecycleError,
    accept_for_primary,
    verify_lock,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "aflow_contract"


def ready_input() -> dict:
    return json.loads((FIXTURES / "ready-input.json").read_text(encoding="utf-8"))


def analysis() -> dict:
    return {
        "success_definition": {},
        "required_trajectory": {},
        "plan_gaps": [],
        "validation_cases": [],
        "fixture_specifications": [],
        "lock_payload": {
            "immutable_user_task_canonical_text": "task\n",
            "accepted_plan_canonical_text": "plan\n",
            "success_definition_canonical_text": "success\n",
            "required_trajectory_canonical_text": "trajectory\n",
            "validation_cases_canonical_text": "validation\n",
            "fixture_specifications_canonical_text": "fixtures\n",
            "hash_algorithm": "sha256",
        },
        "decision": {
            "aflow_decision": "no_material_gap",
            "contract_decision": "no_material_gap",
            "plan_ready_for_primary_decision": True,
        },
    }


def make_lock_and_contract() -> tuple[dict, dict]:
    """Create a valid lock and contract pair for testing."""
    contract = assemble_contract(ready_input())["aflow_execution_contract"]
    lock = accept_for_primary(
        analysis(),
        execution_contract_sha256=contract["lock_payload"]["sha256"],
    )
    return lock, contract


class TestWriteActiveState:
    """Tests for write_active_state."""

    def test_writes_valid_envelope(self, tmp_path: Path) -> None:
        lock, contract = make_lock_and_contract()
        path = write_active_state(
            tmp_path,
            operation_id="op.test-1",
            primary_lock=lock,
            execution_contract=contract,
        )
        assert path.exists()
        envelope = json.loads(path.read_text(encoding="utf-8"))
        assert envelope["operation_id"] == "op.test-1"
        assert envelope["drift_state"] == "valid"
        assert verify_lock(envelope["primary_lock"])

    def test_rejects_invalid_lock(self, tmp_path: Path) -> None:
        _, contract = make_lock_and_contract()
        bad_lock = {"lock_version": 1, "locked_by": "x", "hash_algorithm": "sha256",
                    "canonical_payload": {}, "lock_hash": "0" * 64}
        with pytest.raises(AudisorLifecycleError, match="lock verification failed"):
            write_active_state(
                tmp_path,
                operation_id="op.test",
                primary_lock=bad_lock,
                execution_contract=contract,
            )

    def test_rejects_invalid_contract(self, tmp_path: Path) -> None:
        lock, _ = make_lock_and_contract()
        bad_contract = {"contract_version": "1.0.0", "lock_payload": {"hash_algorithm": "sha256"}}
        with pytest.raises(AudisorLifecycleError, match="contract verification failed"):
            write_active_state(
                tmp_path,
                operation_id="op.test",
                primary_lock=lock,
                execution_contract=bad_contract,
            )

    def test_rejects_conflicting_operation(self, tmp_path: Path) -> None:
        lock, contract = make_lock_and_contract()
        write_active_state(
            tmp_path,
            operation_id="op.first",
            primary_lock=lock,
            execution_contract=contract,
        )
        with pytest.raises(AudisorLifecycleError, match="op.first"):
            write_active_state(
                tmp_path,
                operation_id="op.second",
                primary_lock=lock,
                execution_contract=contract,
            )

    def test_idempotent_same_operation(self, tmp_path: Path) -> None:
        lock, contract = make_lock_and_contract()
        write_active_state(
            tmp_path,
            operation_id="op.same",
            primary_lock=lock,
            execution_contract=contract,
        )
        # Same operation_id: idempotent rewrite is allowed
        path = write_active_state(
            tmp_path,
            operation_id="op.same",
            primary_lock=lock,
            execution_contract=contract,
        )
        assert path.exists()

    def test_rejects_malformed_existing_state(self, tmp_path: Path) -> None:
        tmp_path.mkdir(parents=True, exist_ok=True)
        (tmp_path / "active-lock.json").write_text("not json", encoding="utf-8")
        lock, contract = make_lock_and_contract()
        with pytest.raises(AudisorLifecycleError, match="malformed"):
            write_active_state(
                tmp_path,
                operation_id="op.test",
                primary_lock=lock,
                execution_contract=contract,
            )


class TestReadActiveState:
    """Tests for read_active_state."""

    def test_returns_none_when_absent(self, tmp_path: Path) -> None:
        assert read_active_state(tmp_path) is None

    def test_reads_written_state(self, tmp_path: Path) -> None:
        lock, contract = make_lock_and_contract()
        write_active_state(
            tmp_path,
            operation_id="op.read",
            primary_lock=lock,
            execution_contract=contract,
        )
        state = read_active_state(tmp_path)
        assert state is not None
        assert state["operation_id"] == "op.read"
        assert state["drift_state"] == "valid"

    def test_raises_on_malformed(self, tmp_path: Path) -> None:
        tmp_path.mkdir(parents=True, exist_ok=True)
        (tmp_path / "active-lock.json").write_text("{{{", encoding="utf-8")
        with pytest.raises(AudisorLifecycleError, match="malformed"):
            read_active_state(tmp_path)


class TestClearActiveState:
    """Tests for clear_active_state."""

    def test_clears_existing(self, tmp_path: Path) -> None:
        lock, contract = make_lock_and_contract()
        write_active_state(
            tmp_path,
            operation_id="op.clear",
            primary_lock=lock,
            execution_contract=contract,
        )
        assert clear_active_state(tmp_path) is True
        assert read_active_state(tmp_path) is None

    def test_returns_false_when_absent(self, tmp_path: Path) -> None:
        assert clear_active_state(tmp_path) is False

    def test_clear_allows_new_operation(self, tmp_path: Path) -> None:
        lock, contract = make_lock_and_contract()
        write_active_state(
            tmp_path,
            operation_id="op.old",
            primary_lock=lock,
            execution_contract=contract,
        )
        clear_active_state(tmp_path)
        # Now a different operation can write
        path = write_active_state(
            tmp_path,
            operation_id="op.new",
            primary_lock=lock,
            execution_contract=contract,
        )
        state = json.loads(path.read_text(encoding="utf-8"))
        assert state["operation_id"] == "op.new"
