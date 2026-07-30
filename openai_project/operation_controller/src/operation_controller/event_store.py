"""Append-only JSONL event store for operation lifecycle events.

Each operation gets one file: `.codex/operations/{operation_id}.events.jsonl`

Events are:
- Monotonically sequenced per operation.
- Appended atomically (single write + flush per event).
- Recoverable on partial writes (trailing incomplete lines are skipped on read).

Thread safety: a per-operation lock prevents concurrent appends within a
single process. Multi-process safety is NOT guaranteed.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class OperationEvent:
    """Single persisted event in an operation's lifecycle."""

    sequence: int
    operation_id: str
    timestamp: str
    state: str
    event_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    correlation_id: str | None = None


class EventStore:
    """Append-only JSONL event store scoped to a directory of operation files."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)
        # Per-operation locks to serialize writes
        self._locks: dict[str, threading.Lock] = {}
        self._lock_guard = threading.Lock()
        # Per-operation monotonic sequence counters
        self._sequences: dict[str, int] = {}

    def _get_lock(self, operation_id: str) -> threading.Lock:
        with self._lock_guard:
            if operation_id not in self._locks:
                self._locks[operation_id] = threading.Lock()
            return self._locks[operation_id]

    def _event_path(self, operation_id: str) -> Path:
        return self._root / f"{operation_id}.events.jsonl"

    def _next_sequence(self, operation_id: str) -> int:
        """Return and increment the sequence counter for an operation.

        On first access, scans the existing event file to find the max sequence.
        """
        if operation_id not in self._sequences:
            events = self._read_raw(operation_id)
            if events:
                self._sequences[operation_id] = events[-1].sequence + 1
            else:
                self._sequences[operation_id] = 1
        seq = self._sequences[operation_id]
        self._sequences[operation_id] = seq + 1
        return seq

    def append(
        self,
        operation_id: str,
        *,
        state: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> OperationEvent:
        """Append a new event for the given operation. Returns the persisted event."""
        lock = self._get_lock(operation_id)
        with lock:
            seq = self._next_sequence(operation_id)
            event = OperationEvent(
                sequence=seq,
                operation_id=operation_id,
                timestamp=datetime.now(timezone.utc).isoformat(),
                state=state,
                event_type=event_type,
                payload=payload or {},
                correlation_id=correlation_id,
            )
            path = self._event_path(operation_id)
            line = json.dumps(asdict(event), ensure_ascii=False) + "\n"
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
            return event

    def read_after(
        self,
        operation_id: str,
        after: int = 0,
        limit: int = 50,
    ) -> list[OperationEvent]:
        """Read events with sequence > after, up to limit.

        Returns an empty list if the operation has no events or after is beyond
        the last event.
        """
        all_events = self._read_raw(operation_id)
        # Filter to events after the cursor
        filtered = [e for e in all_events if e.sequence > after]
        return filtered[:limit]

    def latest_sequence(self, operation_id: str) -> int:
        """Return the latest sequence number, or 0 if no events exist."""
        events = self._read_raw(operation_id)
        if events:
            return events[-1].sequence
        return 0

    def exists(self, operation_id: str) -> bool:
        """Check if any events exist for an operation."""
        return self._event_path(operation_id).exists()

    def _read_raw(self, operation_id: str) -> list[OperationEvent]:
        """Read all valid events from the JSONL file.

        Skips incomplete/corrupt lines (partial write recovery).
        """
        path = self._event_path(operation_id)
        if not path.exists():
            return []

        events: list[OperationEvent] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                events.append(OperationEvent(**data))
            except (json.JSONDecodeError, TypeError, KeyError):
                # Partial or corrupt line — skip
                continue
        return events
