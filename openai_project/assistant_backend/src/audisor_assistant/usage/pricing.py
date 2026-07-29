"""Exact-match, effective-date pricing registry."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from .models import PricingRecord, PricingSnapshot


class PricingRegistryError(ValueError):
    pass


class PricingDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str
    records: tuple[PricingRecord, ...]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise PricingRegistryError("pricing timestamps must include a timezone")
    return value.astimezone(timezone.utc)


def _snapshot(record: PricingRecord) -> PricingSnapshot:
    payload = record.model_dump(mode="json")
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return PricingSnapshot(
        **payload,
        snapshot_sha256="sha256:" + hashlib.sha256(canonical).hexdigest(),
    )


class PricingRegistry:
    def __init__(self, records: tuple[PricingRecord, ...]) -> None:
        identities: set[tuple[str, str, datetime]] = set()
        for record in records:
            identity = (record.provider, record.model, _utc(record.effective_from))
            if identity in identities:
                raise PricingRegistryError("duplicate pricing record identity")
            identities.add(identity)
        self._records = records

    @classmethod
    def from_path(cls, path: str | Path) -> "PricingRegistry":
        try:
            document = PricingDocument.model_validate_json(
                Path(path).read_text(encoding="utf-8")
            )
        except (OSError, ValidationError, ValueError) as error:
            raise PricingRegistryError("pricing registry is invalid") from error
        if document.schema_version != "1.0.0":
            raise PricingRegistryError("unsupported pricing schema version")
        return cls(document.records)

    def resolve(
        self, *, provider: str, model: str, at: datetime
    ) -> PricingSnapshot | None:
        instant = _utc(at)
        matches = [
            record
            for record in self._records
            if record.provider == provider
            and record.model == model
            and _utc(record.effective_from) <= instant
            and (
                record.effective_until is None
                or instant < _utc(record.effective_until)
            )
        ]
        if not matches:
            return None
        if len(matches) > 1:
            raise PricingRegistryError("overlapping pricing records")
        return _snapshot(matches[0])
