"""Tests for the append-only JSONL event store.

Covers: append/read, cursor-based pagination, sequence monotonicity,
partial write recovery, and concurrent access.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from operation_controller.event_store import EventStore, OperationEvent


class TestEventStoreAppendAndRead:
    def test_append_and_read_back(self, tmp_path: Path) -> None:
        store = EventStore(tmp_path)
        for i in range(3):
            store.append("op-1", state="planning", event_type=f"evt-{i}", payload={"i": i})

        events = store.read_after("op-1", after=0)
        assert len(events) == 3
        # Sequences are monotonically increasing starting at 1
        assert [e.sequence for e in events] == [1, 2, 3]
        assert all(e.operation_id == "op-1" for e in events)
        assert events[0].event_type == "evt-0"
        assert events[2].payload == {"i": 2}

    def test_read_after_cursor(self, tmp_path: Path) -> None:
        store = EventStore(tmp_path)
        for i in range(5):
            store.append("op-1", state="s", event_type=f"e{i}")

        # Cursor at 3 means return events with sequence > 3
        events = store.read_after("op-1", after=3)
        assert len(events) == 2
        assert [e.sequence for e in events] == [4, 5]

    def test_read_limit(self, tmp_path: Path) -> None:
        store = EventStore(tmp_path)
        for i in range(10):
            store.append("op-1", state="s", event_type=f"e{i}")

        events = store.read_after("op-1", after=0, limit=3)
        assert len(events) == 3
        assert [e.sequence for e in events] == [1, 2, 3]

    def test_latest_sequence(self, tmp_path: Path) -> None:
        store = EventStore(tmp_path)
        assert store.latest_sequence("op-1") == 0

        store.append("op-1", state="s", event_type="e1")
        assert store.latest_sequence("op-1") == 1

        store.append("op-1", state="s", event_type="e2")
        assert store.latest_sequence("op-1") == 2

        # New store instance re-scans the file
        store2 = EventStore(tmp_path)
        assert store2.latest_sequence("op-1") == 2
        # And continues correctly
        store2.append("op-1", state="s", event_type="e3")
        assert store2.latest_sequence("op-1") == 3

    def test_exists(self, tmp_path: Path) -> None:
        store = EventStore(tmp_path)
        assert store.exists("op-1") is False

        store.append("op-1", state="s", event_type="e")
        assert store.exists("op-1") is True
        assert store.exists("op-other") is False

    def test_correlation_id(self, tmp_path: Path) -> None:
        store = EventStore(tmp_path)
        evt = store.append("op-1", state="s", event_type="e", correlation_id="corr-123")
        assert evt.correlation_id == "corr-123"

        events = store.read_after("op-1")
        assert events[0].correlation_id == "corr-123"


class TestEventStoreRecovery:
    def test_partial_write_recovery(self, tmp_path: Path) -> None:
        """Corrupt/truncated lines are skipped; valid events still readable."""
        store = EventStore(tmp_path)
        # Write a valid event first
        store.append("op-1", state="s", event_type="valid1")

        # Manually inject a corrupt line into the file
        event_path = tmp_path / "op-1.events.jsonl"
        with open(event_path, "a", encoding="utf-8") as f:
            f.write('{"sequence": 999, "broken": true\n')  # incomplete JSON

        # Append another valid event (sequence assigned by store logic)
        store.append("op-1", state="s", event_type="valid2")

        # Reading should skip the corrupt line
        events = store.read_after("op-1", after=0)
        assert len(events) == 2
        assert events[0].event_type == "valid1"
        assert events[1].event_type == "valid2"

    def test_empty_lines_skipped(self, tmp_path: Path) -> None:
        store = EventStore(tmp_path)
        store.append("op-1", state="s", event_type="e1")

        # Inject blank lines
        event_path = tmp_path / "op-1.events.jsonl"
        with open(event_path, "a", encoding="utf-8") as f:
            f.write("\n\n\n")

        store.append("op-1", state="s", event_type="e2")
        events = store.read_after("op-1", after=0)
        assert len(events) == 2


class TestEventStoreConcurrency:
    def test_concurrent_appends_no_interleave(self, tmp_path: Path) -> None:
        """Multiple threads appending events produce unique, gap-free sequences."""
        store = EventStore(tmp_path)
        num_threads = 10
        events_per_thread = 10
        errors: list[str] = []

        def worker(thread_id: int) -> None:
            try:
                for i in range(events_per_thread):
                    store.append(
                        "op-1",
                        state="s",
                        event_type=f"t{thread_id}-e{i}",
                    )
            except Exception as exc:
                errors.append(f"Thread {thread_id}: {exc}")

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread errors: {errors}"

        events = store.read_after("op-1", after=0, limit=200)
        total_expected = num_threads * events_per_thread
        assert len(events) == total_expected

        # All sequences unique and form a contiguous range 1..100
        sequences = [e.sequence for e in events]
        assert sorted(sequences) == list(range(1, total_expected + 1))

    def test_separate_operations_independent(self, tmp_path: Path) -> None:
        """Events for different operations don't interfere."""
        store = EventStore(tmp_path)
        store.append("op-A", state="s", event_type="a1")
        store.append("op-B", state="s", event_type="b1")
        store.append("op-A", state="s", event_type="a2")

        a_events = store.read_after("op-A", after=0)
        b_events = store.read_after("op-B", after=0)
        assert len(a_events) == 2
        assert len(b_events) == 1
        assert [e.sequence for e in a_events] == [1, 2]
        assert [e.sequence for e in b_events] == [1]
