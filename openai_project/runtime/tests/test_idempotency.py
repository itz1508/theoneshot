"""Durable idempotency replay before external target resolution."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from audisor.builder.idempotency import (
    IdempotencyConflictError,
    IdempotencyIndex,
    IdempotencyIndexError,
    derive_idempotency_key_hash,
    fingerprint_request,
)


def request_payload(target: Path, *, allowed: list[str] | None = None) -> dict[str, object]:
    return {
        "build_id": "build-001",
        "execution_id": "execution-001",
        "target_root": str(target),
        "allowed_write_paths": allowed or ["src"],
    }


def test_key_hash_normalizes_unicode_and_request_fingerprint_is_canonical() -> None:
    assert derive_idempotency_key_hash("Key-001") == derive_idempotency_key_hash(
        "Key-001"
    )
    assert fingerprint_request({"b": 2, "a": 1}) == fingerprint_request(
        {"a": 1, "b": 2}
    )


def test_bind_and_lookup_return_durable_execution_and_state_references(
    tmp_path: Path,
) -> None:
    index = IdempotencyIndex(tmp_path / "data")
    execution = tmp_path / "data/builds/build-001/executions/execution-001"
    execution.mkdir(parents=True)
    payload = request_payload(tmp_path / "target")
    fingerprint = fingerprint_request(payload)

    binding = index.bind(
        idempotency_key="request-001",
        request_fingerprint=fingerprint,
        build_id="build-001",
        execution_id="execution-001",
        execution_path=execution,
    )
    loaded = index.lookup_before_resolution("request-001", payload)

    assert binding.is_new is True
    assert loaded == binding.record
    assert loaded is not None
    assert loaded.execution_path == str(execution.resolve())
    assert loaded.state_path == str((execution / "state.json").resolve())


def test_lookup_survives_target_removal_without_external_resolution(tmp_path: Path) -> None:
    index = IdempotencyIndex(tmp_path / "data")
    target = tmp_path / "target"
    target.mkdir()
    execution = tmp_path / "data/execution-001"
    execution.mkdir(parents=True)
    payload = request_payload(target)
    fingerprint = fingerprint_request(payload)
    index.bind(
        idempotency_key="request-001",
        request_fingerprint=fingerprint,
        build_id="build-001",
        execution_id="execution-001",
        execution_path=execution,
    )
    target.rmdir()

    replay = index.lookup_before_resolution("request-001", payload)

    assert replay is not None
    assert replay.execution_id == "execution-001"
    assert not target.exists()


def test_reused_key_with_different_fingerprint_conflicts_before_resolution(
    tmp_path: Path,
) -> None:
    index = IdempotencyIndex(tmp_path / "data")
    execution = tmp_path / "data/execution-001"
    execution.mkdir(parents=True)
    first = request_payload(tmp_path / "target", allowed=["src"])
    index.bind(
        idempotency_key="request-001",
        request_fingerprint=fingerprint_request(first),
        build_id="build-001",
        execution_id="execution-001",
        execution_path=execution,
    )
    changed = request_payload(tmp_path / "target", allowed=["tests"])

    with pytest.raises(IdempotencyConflictError) as captured:
        index.lookup_before_resolution("request-001", changed)

    assert captured.value.record.execution_id == "execution-001"


def test_concurrent_identical_bind_publishes_once(tmp_path: Path) -> None:
    index = IdempotencyIndex(tmp_path / "data")
    execution = tmp_path / "data/execution-001"
    execution.mkdir(parents=True)
    fingerprint = fingerprint_request({"request": "same"})

    def bind() -> bool:
        return index.bind(
            idempotency_key="request-001",
            request_fingerprint=fingerprint,
            build_id="build-001",
            execution_id="execution-001",
            execution_path=execution,
        ).is_new

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _index: bind(), range(2)))

    assert sorted(outcomes) == [False, True]
    assert len(list(index.root.glob("*.json"))) == 1


def test_bind_rejects_relative_execution_reference(tmp_path: Path) -> None:
    index = IdempotencyIndex(tmp_path / "data")

    with pytest.raises(IdempotencyIndexError, match="absolute"):
        index.bind(
            idempotency_key="request-001",
            request_fingerprint="a" * 64,
            build_id="build-001",
            execution_id="execution-001",
            execution_path=Path("relative-execution"),
        )
