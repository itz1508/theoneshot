"""Operation Envelope — minimal suspend/resume boundary for BuildExecutor and FixDispatcher.

This module provides the thin shim that allows operations to be suspended
(preserving workspace and state) and later resumed, rather than being
terminally finalized on correctable errors.

The full Operation Controller (Phase 3) will subsume this module. Until then,
this provides the minimal contract needed for suspension routing.

Feature flag: Set AUDISOR_ROUTING_ENABLED=1 to enable routing behavior.
When unset, callers should fall back to legacy _finalize_audisor_failure paths.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping


# ── Configuration ────────────────────────────────────────────────────────────

# Default TTL for suspended claims: 1 hour
DEFAULT_CLAIM_TTL_SECONDS = 3600

# Resume input schemas per suspend reason
RESUME_SCHEMAS: dict[str, dict[str, Any]] = {
    "needs_plan_revision": {
        "type": "object",
        "required": ["revised_plan", "closure_evidence"],
        "properties": {
            "revised_plan": {"type": "object", "description": "The corrected plan"},
            "closure_evidence": {"type": "object", "description": "Evidence that gaps are closed"},
        },
    },
    "needs_package_repair": {
        "type": "object",
        "required": ["corrected_package"],
        "properties": {
            "corrected_package": {"type": "object", "description": "The repaired analysis package"},
        },
    },
    "needs_provider_recovery": {
        "type": "object",
        "properties": {},
        "description": "No input required — auto-retry after backoff",
    },
    "needs_evidence": {
        "type": "object",
        "required": ["additional_evidence"],
        "properties": {
            "additional_evidence": {"type": "object", "description": "Additional evidence artifacts"},
        },
    },
}


# ── Data Types ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SuspendedOperation:
    """Record of a suspended operation."""

    operation_id: str
    suspend_reason: str
    resume_input_schema: dict[str, Any]
    preserved_state_path: str
    global_claim_id: str | None
    claim_ttl_seconds: int
    created_at: str
    artifact: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ClaimState:
    """Tracks the state of a global mutation claim."""

    claim_id: str
    status: str  # "active" | "suspended_hold" | "expired_suspended"
    target: str
    created_at: str
    suspended_at: str | None = None
    ttl_seconds: int = DEFAULT_CLAIM_TTL_SECONDS


# ── Envelope Implementation ──────────────────────────────────────────────────


class OperationEnvelope:
    """Minimal suspend/resume boundary for BuildExecutor and FixDispatcher.

    Persistence: suspended operations are stored as JSON files in the
    operations data directory. Each file is named <operation_id>.suspended.json.
    """

    def __init__(self, data_dir: Path | None = None):
        if data_dir is None:
            env_dir = os.environ.get("AUDISOR_OPERATION_DATA_DIR")
            self._data_dir = Path(env_dir) if env_dir else Path.cwd() / ".audisor" / "suspended"
        else:
            self._data_dir = data_dir
        self._data_dir.mkdir(parents=True, exist_ok=True)

    def suspend(
        self,
        *,
        operation_id: str,
        reason: str,
        state_path: Path,
        claim: str | None = None,
        artifact: dict[str, Any] | None = None,
        claim_ttl: int = DEFAULT_CLAIM_TTL_SECONDS,
    ) -> SuspendedOperation:
        """Persist suspension record. Does NOT finalize or clean up.

        On suspend, the global claim is downgraded to a 'suspended_hold' — it
        blocks new conflicting builds for the same target but has a configurable
        TTL. On TTL expiry, the claim converts to 'expired_suspended'.
        """
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        schema = RESUME_SCHEMAS.get(reason, {"type": "object", "properties": {}})

        record = SuspendedOperation(
            operation_id=operation_id,
            suspend_reason=reason,
            resume_input_schema=schema,
            preserved_state_path=str(state_path),
            global_claim_id=claim,
            claim_ttl_seconds=claim_ttl,
            created_at=now,
            artifact=artifact or {},
        )

        # Persist atomically
        record_path = self._data_dir / f"{operation_id}.suspended.json"
        tmp_path = record_path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(asdict(record), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp_path, record_path)

        # If there's a claim, write claim state as suspended_hold
        if claim:
            self._write_claim_state(
                ClaimState(
                    claim_id=claim,
                    status="suspended_hold",
                    target=operation_id,
                    created_at=now,
                    suspended_at=now,
                    ttl_seconds=claim_ttl,
                )
            )

        return record

    def resume(self, operation_id: str, resume_input: Mapping[str, Any]) -> Path:
        """Validate resume input against schema, return preserved state path.

        Raises ValueError if the operation is not found, expired, or input
        does not satisfy the schema.
        """
        record_path = self._data_dir / f"{operation_id}.suspended.json"
        if not record_path.exists():
            raise ValueError(f"No suspended operation found: {operation_id}")

        data = json.loads(record_path.read_text(encoding="utf-8"))

        # Check claim expiry
        if data.get("global_claim_id"):
            claim_state = self._read_claim_state(data["global_claim_id"])
            if claim_state and claim_state.get("status") == "expired_suspended":
                raise ValueError(
                    f"Operation {operation_id} claim has expired. "
                    "Re-acquisition required before resume."
                )

        # Validate resume input against schema
        schema = data.get("resume_input_schema", {})
        required_keys = schema.get("required", [])
        for key in required_keys:
            if key not in resume_input:
                raise ValueError(
                    f"Resume input missing required field: {key}. "
                    f"Schema requires: {required_keys}"
                )

        # Remove suspension record (operation is now active again)
        record_path.unlink(missing_ok=True)

        # Update claim back to active if it exists
        if data.get("global_claim_id"):
            claim_path = self._data_dir / f"{data['global_claim_id']}.claim.json"
            if claim_path.exists():
                claim_data = json.loads(claim_path.read_text(encoding="utf-8"))
                claim_data["status"] = "active"
                tmp = claim_path.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(claim_data, indent=2) + "\n", encoding="utf-8")
                os.replace(tmp, claim_path)

        return Path(data["preserved_state_path"])

    def list_suspended(self) -> list[SuspendedOperation]:
        """Return all operations in suspended state."""
        results: list[SuspendedOperation] = []
        for path in self._data_dir.glob("*.suspended.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                # Check for TTL expiry on claims
                if data.get("global_claim_id"):
                    self._check_and_expire_claim(data)
                results.append(
                    SuspendedOperation(
                        operation_id=data["operation_id"],
                        suspend_reason=data["suspend_reason"],
                        resume_input_schema=data.get("resume_input_schema", {}),
                        preserved_state_path=data["preserved_state_path"],
                        global_claim_id=data.get("global_claim_id"),
                        claim_ttl_seconds=data.get("claim_ttl_seconds", DEFAULT_CLAIM_TTL_SECONDS),
                        created_at=data["created_at"],
                        artifact=data.get("artifact", {}),
                    )
                )
            except (json.JSONDecodeError, KeyError, OSError):
                continue
        return results

    def is_suspended(self, operation_id: str) -> bool:
        """Check if an operation is currently suspended."""
        return (self._data_dir / f"{operation_id}.suspended.json").exists()

    # ── Claim management ─────────────────────────────────────────────────────

    def _write_claim_state(self, claim: ClaimState) -> None:
        claim_path = self._data_dir / f"{claim.claim_id}.claim.json"
        tmp = claim_path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(asdict(claim), indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, claim_path)

    def _read_claim_state(self, claim_id: str) -> dict[str, Any] | None:
        claim_path = self._data_dir / f"{claim_id}.claim.json"
        if not claim_path.exists():
            return None
        try:
            return json.loads(claim_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def _check_and_expire_claim(self, suspension_data: dict[str, Any]) -> None:
        """Check if a claim's TTL has expired and update its state."""
        claim_id = suspension_data.get("global_claim_id")
        if not claim_id:
            return

        claim_state = self._read_claim_state(claim_id)
        if not claim_state or claim_state.get("status") != "suspended_hold":
            return

        suspended_at = claim_state.get("suspended_at", "")
        ttl = claim_state.get("ttl_seconds", DEFAULT_CLAIM_TTL_SECONDS)

        if suspended_at:
            try:
                import calendar
                from datetime import datetime, timezone

                suspended_time = calendar.timegm(
                    datetime.strptime(suspended_at, "%Y-%m-%dT%H:%M:%SZ")
                    .replace(tzinfo=timezone.utc)
                    .timetuple()
                )
                if time.time() - suspended_time > ttl:
                    claim_state["status"] = "expired_suspended"
                    self._write_claim_state(
                        ClaimState(
                            claim_id=claim_state["claim_id"],
                            status="expired_suspended",
                            target=claim_state["target"],
                            created_at=claim_state["created_at"],
                            suspended_at=claim_state.get("suspended_at"),
                            ttl_seconds=ttl,
                        )
                    )
            except (ValueError, KeyError):
                pass


def is_routing_enabled() -> bool:
    """Check whether routing mode is active (feature flag).

    Reads the environment on every call so the flag can be toggled at
    runtime (and patched in tests) without re-importing the module.
    """
    return os.environ.get("AUDISOR_ROUTING_ENABLED", "") == "1"
