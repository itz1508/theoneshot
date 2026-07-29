"""Atomic, idempotent persistence for sanitized usage evidence."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from .models import NormalizedUsage, PricingSnapshot, UsageCost, UsageEstimate


class UsageLedgerError(RuntimeError):
    pass


class UsageAttemptRecord(BaseModel):
    """Sanitized record; prompt and response content have no fields here."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0.0"] = "1.0.0"
    operation_id: str
    attempt_id: str
    provider: str
    model: str
    request_hash: str
    phase: Literal["reserved", "finalized", "blocked"]
    estimate: UsageEstimate
    actual: NormalizedUsage | None = None
    estimated_cost: UsageCost | None = None
    actual_cost: UsageCost | None = None
    pricing_snapshot: PricingSnapshot | None = None
    created_at: datetime
    finalized_at: datetime | None = None
    warnings: tuple[str, ...] = ()

    @model_validator(mode="after")
    def phase_matches_evidence(self) -> "UsageAttemptRecord":
        if self.phase == "reserved" and (
            self.actual is not None
            or self.actual_cost is not None
            or self.finalized_at is not None
        ):
            raise ValueError("reserved records cannot contain final evidence")
        if self.phase != "reserved" and self.finalized_at is None:
            raise ValueError("terminal records require finalized_at")
        return self


def _canonical(record: UsageAttemptRecord) -> bytes:
    return (
        json.dumps(
            record.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        + "\n"
    ).encode("utf-8")


class UsageLedger:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.root.is_dir():
            raise UsageLedgerError("usage ledger root is unavailable")

    @staticmethod
    def _key(operation_id: str, attempt_id: str) -> str:
        material = f"{operation_id}\0{attempt_id}".encode("utf-8")
        return hashlib.sha256(material).hexdigest()

    def _path(self, operation_id: str, attempt_id: str) -> Path:
        return self.root / f"attempt-{self._key(operation_id, attempt_id)}.json"

    def _read(self, path: Path) -> UsageAttemptRecord | None:
        if not path.exists():
            return None
        try:
            return UsageAttemptRecord.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError, ValueError) as error:
            raise UsageLedgerError("usage ledger record is invalid") from error

    def _write(self, path: Path, record: UsageAttemptRecord) -> None:
        temporary = self.root / f".{path.name}.{uuid.uuid4().hex}.tmp"
        try:
            with temporary.open("xb") as stream:
                stream.write(_canonical(record))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        except OSError as error:
            raise UsageLedgerError("usage ledger write failed") from error
        finally:
            if temporary.exists():
                temporary.unlink()

    def reserve(self, record: UsageAttemptRecord) -> UsageAttemptRecord:
        if record.phase != "reserved":
            raise UsageLedgerError("reservation record must use reserved phase")
        path = self._path(record.operation_id, record.attempt_id)
        existing = self._read(path)
        if existing is not None:
            if existing == record:
                return existing
            raise UsageLedgerError("conflicting usage reservation")
        self._write(path, record)
        return record

    def finalize(
        self,
        *,
        operation_id: str,
        attempt_id: str,
        actual: NormalizedUsage,
        actual_cost: UsageCost | None,
        finalized_at: datetime,
        warnings: tuple[str, ...] = (),
    ) -> UsageAttemptRecord:
        path = self._path(operation_id, attempt_id)
        existing = self._read(path)
        if existing is None:
            raise UsageLedgerError("usage reservation is missing")
        desired = existing.model_copy(
            update={
                "phase": "finalized",
                "actual": actual,
                "actual_cost": actual_cost,
                "finalized_at": finalized_at,
                "warnings": warnings,
            }
        )
        if existing.phase == "finalized":
            if existing == desired:
                return existing
            raise UsageLedgerError("conflicting usage finalization")
        if existing.phase != "reserved":
            raise UsageLedgerError("usage attempt is already terminal")
        self._write(path, desired)
        return desired

    def block(
        self,
        *,
        operation_id: str,
        attempt_id: str,
        finalized_at: datetime,
        reasons: tuple[str, ...],
    ) -> UsageAttemptRecord:
        path = self._path(operation_id, attempt_id)
        existing = self._read(path)
        if existing is None:
            raise UsageLedgerError("usage reservation is missing")
        desired = existing.model_copy(
            update={
                "phase": "blocked",
                "finalized_at": finalized_at,
                "warnings": reasons,
            }
        )
        if existing.phase == "blocked":
            if existing == desired:
                return existing
            raise UsageLedgerError("conflicting usage block")
        if existing.phase != "reserved":
            raise UsageLedgerError("usage attempt is already terminal")
        self._write(path, desired)
        return desired

    def get(self, operation_id: str, attempt_id: str) -> UsageAttemptRecord | None:
        return self._read(self._path(operation_id, attempt_id))
