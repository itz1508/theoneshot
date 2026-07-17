"""Data-root authority collisions, durable release, and explicit recovery."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from audisor.builder.global_authority import (
    GlobalAuthorityConflictError,
    GlobalAuthorityError,
    GlobalAuthorityRegistry,
    derive_authority_key,
)


def scope(tmp_path: Path) -> tuple[Path, tuple[Path, ...]]:
    target = tmp_path / "target"
    src = target / "src"
    tests = target / "tests"
    src.mkdir(parents=True)
    tests.mkdir()
    return target, (src, tests)


def acquire(
    registry: GlobalAuthorityRegistry,
    target: Path,
    allowed: tuple[Path, ...],
    *,
    build_id: str = "build-001",
    execution_id: str = "execution-001",
):
    return registry.acquire(
        build_id=build_id,
        execution_id=execution_id,
        idempotency_key=f"request-{execution_id}",
        request_fingerprint="a" * 64,
        target_root=target,
        allowed_paths=allowed,
    )


def test_authority_key_is_stable_for_sorted_normalized_allowed_paths(
    tmp_path: Path,
) -> None:
    target, allowed = scope(tmp_path)

    assert derive_authority_key(target, allowed) == derive_authority_key(
        target, tuple(reversed(allowed))
    )


def test_global_claim_record_has_complete_scope_and_blocks_cross_build_collision(
    tmp_path: Path,
) -> None:
    target, allowed = scope(tmp_path)
    registry = GlobalAuthorityRegistry(tmp_path / "data")
    first = acquire(registry, target, allowed)

    payload = json.loads(first.path.read_text(encoding="utf-8"))
    assert payload["authority_key"] == derive_authority_key(target, allowed)
    assert payload["build_id"] == "build-001"
    assert payload["execution_id"] == "execution-001"
    assert payload["idempotency_key"] == "request-execution-001"
    assert payload["request_fingerprint"] == "a" * 64
    assert payload["status"] == "active"
    assert len(payload["normalized_allowed_paths"]) == 2

    with pytest.raises(GlobalAuthorityConflictError) as captured:
        acquire(
            registry,
            target,
            allowed,
            build_id="build-002",
            execution_id="execution-002",
        )
    assert captured.value.record.claim_id == first.record.claim_id


def test_atomic_concurrent_acquire_has_one_winner_and_one_conflict(
    tmp_path: Path,
) -> None:
    target, allowed = scope(tmp_path)
    registry = GlobalAuthorityRegistry(tmp_path / "data")

    def contender(index: int) -> str:
        try:
            acquire(
                registry,
                target,
                allowed,
                build_id=f"build-00{index}",
                execution_id=f"execution-00{index}",
            )
        except GlobalAuthorityConflictError:
            return "conflict"
        return "acquired"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(contender, (1, 2)))

    assert sorted(outcomes) == ["acquired", "conflict"]


def test_interrupted_claim_is_retained_until_explicit_verified_recovery(
    tmp_path: Path,
) -> None:
    target, allowed = scope(tmp_path)
    registry = GlobalAuthorityRegistry(tmp_path / "data")
    claim = acquire(registry, target, allowed)

    with pytest.raises(GlobalAuthorityError, match="retained"):
        registry.release(
            claim,
            terminal_status="interrupted",
            terminal_manifest_sha256="b" * 64,
            release_evidence_sha256="e" * 64,
            reconciliation_verified=True,
        )
    with pytest.raises(GlobalAuthorityError, match="safe release"):
        registry.recover(
            authority_key=claim.record.authority_key,
            expected_claim_id=claim.record.claim_id,
            recovery_evidence_sha256="c" * 64,
            safe_to_release=False,
            reason="operator reviewed workspace",
        )
    assert registry.load_active(claim.record.authority_key) == claim.record

    history = registry.recover(
        authority_key=claim.record.authority_key,
        expected_claim_id=claim.record.claim_id,
        recovery_evidence_sha256="c" * 64,
        safe_to_release=True,
        reason="workspace and target reconciliation verified",
    )

    assert history.is_file()
    assert registry.load_active(claim.record.authority_key) is None
    persisted = json.loads(history.read_text(encoding="utf-8"))
    assert persisted["outcome"] == "recovered"
    assert persisted["safe_to_release"] is True
    assert persisted["recovery_evidence_sha256"] == "c" * 64


def test_terminal_release_requires_manifest_reconciliation_and_is_durable(
    tmp_path: Path,
) -> None:
    target, allowed = scope(tmp_path)
    registry = GlobalAuthorityRegistry(tmp_path / "data")
    claim = acquire(registry, target, allowed)
    release_evidence = registry.prepare_release_evidence(
        claim, terminal_status="completed"
    )
    assert release_evidence.path.is_file()
    assert claim.path.is_file()
    assert release_evidence.record.claim_sha256

    with pytest.raises(GlobalAuthorityError, match="verified reconciliation"):
        registry.release(
            claim,
            terminal_status="completed",
            terminal_manifest_sha256="d" * 64,
            release_evidence_sha256=release_evidence.sha256,
            reconciliation_verified=False,
        )
    assert registry.load_active(claim.record.authority_key) == claim.record

    history = registry.release(
        claim,
        terminal_status="completed",
        terminal_manifest_sha256="d" * 64,
        release_evidence_sha256=release_evidence.sha256,
        reconciliation_verified=True,
    )

    persisted = json.loads(history.read_text(encoding="utf-8"))
    assert persisted["terminal_status"] == "completed"
    assert persisted["terminal_manifest_sha256"] == "d" * 64
    assert persisted["release_evidence_sha256"] == release_evidence.sha256
    assert persisted["reconciliation_verified"] is True
    assert registry.load_active(claim.record.authority_key) is None

    replacement = acquire(
        registry,
        target,
        allowed,
        build_id="build-002",
        execution_id="execution-002",
    )
    assert replacement.record.claim_id != claim.record.claim_id


def test_allowed_scope_must_be_absolute_and_within_target(tmp_path: Path) -> None:
    target, _allowed = scope(tmp_path)
    registry = GlobalAuthorityRegistry(tmp_path / "data")

    with pytest.raises(GlobalAuthorityError, match="absolute"):
        acquire(registry, target, (Path("relative"),))
    with pytest.raises(GlobalAuthorityError, match="escapes"):
        acquire(registry, target, (tmp_path / "outside",))
