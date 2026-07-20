from __future__ import annotations

import threading

import pytest

from audisor.operations.store import ContinuationClaimError, SharedOperationStore


def stored(store: SharedOperationStore, operation_id: str = "op-1") -> str:
    request_hash = "a" * 64
    store.bind(operation_id, request_hash, {"operation_kind": "build"})
    store.persist_response(
        operation_id,
        {
            "operation_id": operation_id,
            "operation_kind": "build",
            "client_id": "codex",
            "request_hash": request_hash,
            "status": "accepted",
            "aflow_enabled": True,
            "aflow_invoked": True,
            "decision_state": "no_material_gap",
            "execution_contract_reference": "contract.json",
            "artifact_references": [],
            "authority_limits": {"apply": False},
            "continuation": {"permitted": True, "state": "permitted"},
            "failure": None,
            "existing_result": False,
        },
    )
    return request_hash


def test_claim_is_persisted_and_second_claim_is_rejected(tmp_path):
    store = SharedOperationStore(tmp_path)
    request_hash = stored(store)
    assert store.claim_continuation("op-1", request_hash) == "claimed"
    assert store.load("op-1")["continuation_consumed"] is True
    with pytest.raises(ContinuationClaimError, match="continuation_already_consumed"):
        store.claim_continuation("op-1", request_hash)


def test_concurrent_claims_allow_exactly_one(tmp_path):
    store = SharedOperationStore(tmp_path)
    request_hash = stored(store)
    results = []

    def claim():
        try:
            results.append(store.claim_continuation("op-1", request_hash))
        except ContinuationClaimError as exc:
            results.append(exc.args[0])

    threads = [threading.Thread(target=claim) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results.count("claimed") == 1
    assert sum("continuation_already_consumed" in value for value in results if isinstance(value, str)) == 1
