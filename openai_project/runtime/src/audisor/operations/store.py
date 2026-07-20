"""Audisor operation store — generic, host-agnostic, replaces BuildStore.

Provides persistent operation state, idempotency, and result caching.
All storage is keyed by operation_id with filesystem persistence.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping

from audisor.schemas.errors import AudisorRuntimeError
from audisor.schemas.idempotency import IdempotencyKey, IdempotencyRecord, IdempotencyStore


@dataclass
class OperationState:
    """Persisted state for a single operation."""

    operation_id: str
    status: Literal["pending", "running", "completed", "failed", "blocked"]
    request_hash: str
    result_hash: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    completed_at: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    artifacts: list[Mapping[str, Any]] = field(default_factory=list)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "status": self.status,
            "request_hash": self.request_hash,
            "result_hash": self.result_hash,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "artifacts": [dict(a) for a in self.artifacts],
        }


class AudisorOperationStore:
    """Filesystem-based operation store with idempotency support.

    Replaces BuildStore with a generic, host-agnostic implementation.
    All operations are keyed by operation_id and persisted atomically.
    """

    def __init__(
        self,
        root: Path,
        *,
        idempotency_store: IdempotencyStore | None = None,
    ) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)
        self._idempotency = idempotency_store or IdempotencyStore()
        self._operations: dict[str, OperationState] = {}
        self._load_all()

    def _operation_path(self, operation_id: str) -> Path:
        safe_id = "".join(c for c in operation_id if c.isalnum() or c in "-_.")
        return self._root / f"{safe_id}.json"

    def _load_all(self) -> None:
        """Load all persisted operation states from disk."""
        if not self._root.exists():
            return
        for path in self._root.iterdir():
            if path.suffix != ".json":
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                state = OperationState(
                    operation_id=data["operation_id"],
                    status=data["status"],
                    request_hash=data["request_hash"],
                    result_hash=data.get("result_hash"),
                    created_at=data["created_at"],
                    completed_at=data.get("completed_at"),
                    error_code=data.get("error_code"),
                    error_message=data.get("error_message"),
                    artifacts=data.get("artifacts", []),
                )
                self._operations[state.operation_id] = state
            except (json.JSONDecodeError, KeyError, ValueError):
                # Skip corrupted files
                continue

    def _persist(self, state: OperationState) -> None:
        """Atomically persist an operation state to disk."""
        path = self._operation_path(state.operation_id)
        temp_path = path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(state.to_mapping(), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        # Atomic replace
        os.replace(temp_path, path)
        self._operations[state.operation_id] = state

    def _compute_request_hash(self, request: Mapping[str, Any]) -> str:
        """Compute a stable hash of a request for idempotency."""
        payload = json.dumps(request, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def create(
        self,
        operation_id: str,
        request: Mapping[str, Any],
        *,
        idempotency_key: IdempotencyKey | None = None,
    ) -> tuple[Literal["new", "existing", "conflict"], OperationState | None]:
        """Create a new operation or return an existing one.

        Returns:
            - "new": Operation created, proceed with execution.
            - "existing": Operation already exists with same request hash, return cached.
            - "conflict": Operation exists with different request hash, reject.
        """
        # Check idempotency first
        if idempotency_key is not None:
            idem_status, idem_record = self._idempotency.check(idempotency_key, operation_id)
            if idem_status == "conflict":
                return "conflict", None
            if idem_status == "replay":
                # Check if we have a completed operation
                existing = self._operations.get(operation_id)
                if existing is not None and existing.status in ("completed", "failed", "blocked"):
                    return "existing", existing

        request_hash = self._compute_request_hash(request)
        existing = self._operations.get(operation_id)

        if existing is not None:
            if existing.request_hash == request_hash:
                return "existing", existing
            return "conflict", None

        state = OperationState(
            operation_id=operation_id,
            status="pending",
            request_hash=request_hash,
        )
        self._persist(state)
        return "new", state

    def start(self, operation_id: str) -> OperationState:
        """Mark an operation as running."""
        state = self._operations.get(operation_id)
        if state is None:
            raise AudisorRuntimeError(
                category="storage",
                stage="execution",
                code="operation_not_found",
                message=f"Operation not found: {operation_id}",
            )
        state.status = "running"  # type: ignore[misc]
        self._persist(state)
        return state

    def complete(
        self,
        operation_id: str,
        result: Mapping[str, Any],
        *,
        artifacts: list[Mapping[str, Any]] | None = None,
        idempotency_key: IdempotencyKey | None = None,
    ) -> OperationState:
        """Mark an operation as completed with its result."""
        state = self._operations.get(operation_id)
        if state is None:
            raise AudisorRuntimeError(
                category="storage",
                stage="execution",
                code="operation_not_found",
                message=f"Operation not found: {operation_id}",
            )

        result_hash = self._compute_request_hash(result)
        state.status = "completed"  # type: ignore[misc]
        state.result_hash = result_hash
        state.completed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        state.artifacts = list(artifacts) if artifacts else []
        self._persist(state)

        if idempotency_key is not None:
            self._idempotency.complete(idempotency_key, operation_id, result_hash)

        return state

    def fail(
        self,
        operation_id: str,
        error_code: str,
        error_message: str,
        *,
        idempotency_key: IdempotencyKey | None = None,
    ) -> OperationState:
        """Mark an operation as failed."""
        state = self._operations.get(operation_id)
        if state is None:
            raise AudisorRuntimeError(
                category="storage",
                stage="execution",
                code="operation_not_found",
                message=f"Operation not found: {operation_id}",
            )

        state.status = "failed"  # type: ignore[misc]
        state.error_code = error_code
        state.error_message = error_message
        state.completed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        self._persist(state)

        if idempotency_key is not None:
            self._idempotency.fail(idempotency_key, operation_id)

        return state

    def block(
        self,
        operation_id: str,
        reason: str,
        *,
        idempotency_key: IdempotencyKey | None = None,
    ) -> OperationState:
        """Mark an operation as blocked (authority or validation failure)."""
        state = self._operations.get(operation_id)
        if state is None:
            raise AudisorRuntimeError(
                category="storage",
                stage="execution",
                code="operation_not_found",
                message=f"Operation not found: {operation_id}",
            )

        state.status = "blocked"  # type: ignore[misc]
        state.error_code = "blocked"
        state.error_message = reason
        state.completed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        self._persist(state)

        if idempotency_key is not None:
            self._idempotency.fail(idempotency_key, operation_id)

        return state

    def get(self, operation_id: str) -> OperationState | None:
        """Retrieve an operation state by ID."""
        return self._operations.get(operation_id)

    def list_operations(
        self,
        *,
        status: Literal["pending", "running", "completed", "failed", "blocked"] | None = None,
    ) -> list[OperationState]:
        """List operations, optionally filtered by status."""
        ops = list(self._operations.values())
        if status is not None:
            ops = [o for o in ops if o.status == status]
        return ops


# =============================================================================
# BACKWARD-COMPATIBILITY WRAPPERS
# =============================================================================
# The old store module exported SharedOperationStore and ContinuationClaimError.
# These are imported by proven external consumers:
#   - audisor/codex/adapter.py
#   - audisor/operations/transport.py
#   - audisor/operations/service.py
#   - tests/operations/test_store.py
#   - tests/operations/test_service.py
#   - tests/codex/test_adapter.py
# These wrappers delegate to the new AudisorOperationStore API.
# =============================================================================


class ContinuationClaimError(RuntimeError):
    """Backward-compatible exception for continuation claim failures."""

    def __init__(self, code: str = "continuation_claim_failed", message: str = ""):
        super().__init__(message)
        self.code = code


class SharedOperationStore:
    """Backward-compatible operation store preserving the original API.

    This is a standalone implementation that maintains the legacy file format
    and behavior. It does NOT delegate to AudisorOperationStore, because the
    legacy API tracks request_hash explicitly (passed by caller) rather than
    computing it from a request payload.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, operation_id: str) -> Path:
        safe_id = "".join(c for c in operation_id if c.isalnum() or c in "-_.")
        return self.root / f"{safe_id}.json"

    def bind(
        self,
        operation_id: str,
        request_hash: str,
        metadata: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Bind an operation. Returns existing record if already bound with same hash."""
        path = self._path(operation_id)
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            stored_hash = data.get("canonical_request_hash")
            if stored_hash != request_hash:
                from audisor.operations.models import OperationIdentityConflict

                raise OperationIdentityConflict("operation_id is already bound to a different request")
            return {
                "canonical_request_hash": stored_hash,
                "stored_response": data.get("stored_response"),
            }
        # New binding
        data = {
            "operation_id": operation_id,
            "canonical_request_hash": request_hash,
            "metadata": dict(metadata),
            "stored_response": None,
            "continuation_consumed": False,
        }
        path.write_text(
            json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        return None

    def load(self, operation_id: str) -> dict[str, Any] | None:
        """Load persisted operation record."""
        path = self._path(operation_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return {
            "canonical_request_hash": data.get("canonical_request_hash"),
            "stored_response": data.get("stored_response"),
            "continuation_consumed": data.get("continuation_consumed", False),
        }

    def persist_response(self, operation_id: str, response: Mapping[str, Any]) -> None:
        """Persist a response dict for an operation."""
        path = self._path(operation_id)
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        data["stored_response"] = dict(response)
        path.write_text(
            json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )

    def claim_continuation(self, operation_id: str, request_hash: str) -> str:
        """Claim continuation for an operation.

        Returns "claimed" on first successful claim.
        Raises ContinuationClaimError on conflict or if already consumed.
        """
        with self._lock:
            path = self._path(operation_id)
            if not path.exists():
                raise ContinuationClaimError(
                    code="operation_not_found",
                    message=f"Operation not found: {operation_id}",
                )
            data = json.loads(path.read_text(encoding="utf-8"))
            stored_hash = data.get("canonical_request_hash")
            if stored_hash != request_hash:
                raise ContinuationClaimError(
                    code="request_hash_mismatch",
                    message="Request hash does not match bound operation",
                )
            if data.get("continuation_consumed", False):
                raise ContinuationClaimError(
                    code="continuation_already_consumed",
                    message="continuation_already_consumed: Continuation has already been claimed for this operation",
                )
            data["continuation_consumed"] = True
            path.write_text(
                json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
            return "claimed"
