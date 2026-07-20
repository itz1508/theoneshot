import pytest

from audisor.operations.models import OperationIdentityConflict
from audisor.operations.store import SharedOperationStore


def test_store_binds_and_rejects_conflicting_identity(tmp_path):
    store = SharedOperationStore(tmp_path)
    assert store.bind("op-1", "hash-1", {"operation_kind": "build", "host_identity": {"build_id": "b", "execution_id": "op-1"}}) is None
    assert store.bind("op-1", "hash-1", {}) is not None
    with pytest.raises(OperationIdentityConflict):
        store.bind("op-1", "hash-2", {})


def test_request_hash_is_deterministic():
    from audisor.operations.models import canonical_request_hash
    assert canonical_request_hash({"b": 2, "a": 1}) == canonical_request_hash({"a": 1, "b": 2})
