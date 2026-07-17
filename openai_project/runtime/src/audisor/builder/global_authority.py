"""Data-root authority claims that prevent cross-build target collisions."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Sequence

from audisor.builder.safe_json import json_safe_bytes

_SHA256_LENGTH = 64
_TERMINAL_RELEASE_STATUSES = frozenset({"completed", "failed"})


class GlobalAuthorityError(RuntimeError):
    """The global authority registry is unsafe, corrupt, or misused."""


class GlobalAuthorityConflictError(GlobalAuthorityError):
    """Another execution already holds the requested authority."""

    def __init__(self, record: "AuthorityClaimRecord") -> None:
        super().__init__("The target authority is already claimed")
        self.record = record


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_sha256(value: str, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != _SHA256_LENGTH
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise GlobalAuthorityError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


def _normalized_resolved_path(value: str | Path) -> str:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise GlobalAuthorityError("Authority paths must be absolute")
    return os.path.normcase(os.path.normpath(str(path.resolve(strict=False))))


def _is_within(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


def normalized_authority_scope(
    target_root: str | Path,
    allowed_paths: Sequence[str | Path],
) -> tuple[str, tuple[str, ...]]:
    """Resolve and deterministically order one target authority scope."""
    target = _normalized_resolved_path(target_root)
    if not allowed_paths:
        raise GlobalAuthorityError("At least one allowed path is required")
    allowed = tuple(
        sorted({_normalized_resolved_path(path) for path in allowed_paths})
    )
    if any(not _is_within(path, target) for path in allowed):
        raise GlobalAuthorityError("Allowed authority path escapes the target root")
    return target, allowed


def derive_authority_key(
    target_root: str | Path,
    allowed_paths: Sequence[str | Path],
) -> str:
    """Hash normalized target plus sorted normalized allowed paths."""
    target, allowed = normalized_authority_scope(target_root, allowed_paths)
    payload = {"target_root": target, "allowed_paths": list(allowed)}
    return hashlib.sha256(json_safe_bytes(payload, trailing_newline=False)).hexdigest()


def _is_reparse_or_symlink(path: Path) -> bool:
    try:
        status = path.lstat()
    except OSError:
        return False
    attributes = getattr(status, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(attributes & reparse_flag)


def _ensure_real_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir() or _is_reparse_or_symlink(path):
        raise GlobalAuthorityError("Authority registry directory is unsafe")


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except (OSError, AttributeError):
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _publish_no_replace(path: Path, content: bytes) -> None:
    """Publish complete bytes atomically while refusing to replace a claim."""
    _ensure_real_directory(path.parent)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


class _KeyMutex:
    """Cross-process byte-range lock guarding release/acquire identity checks."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle = None

    def acquire(self, timeout: float = 10.0) -> None:
        _ensure_real_directory(self.path.parent)
        if self.path.exists() and (_is_reparse_or_symlink(self.path) or not self.path.is_file()):
            raise GlobalAuthorityError("Authority mutex path is unsafe")
        self.handle = self.path.open("a+b")
        self.handle.seek(0, os.SEEK_END)
        if self.handle.tell() == 0:
            self.handle.write(b"\0")
            self.handle.flush()
            os.fsync(self.handle.fileno())
        deadline = time.monotonic() + timeout
        while True:
            try:
                self.handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except (OSError, BlockingIOError):
                if time.monotonic() >= deadline:
                    self.handle.close()
                    self.handle = None
                    raise GlobalAuthorityError("Authority registry is busy") from None
                time.sleep(0.025)

    def release(self) -> None:
        if self.handle is None:
            return
        try:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            self.handle.close()
            self.handle = None

    def __enter__(self) -> "_KeyMutex":
        self.acquire()
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()


@dataclass(frozen=True)
class AuthorityClaimRecord:
    schema_version: int
    authority_key: str
    claim_id: str
    build_id: str
    execution_id: str
    idempotency_key: str
    request_fingerprint: str
    normalized_target_root: str
    normalized_allowed_paths: tuple[str, ...]
    acquired_at: str
    status: str = "active"

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["normalized_allowed_paths"] = list(self.normalized_allowed_paths)
        return payload


@dataclass(frozen=True)
class AuthorityClaim:
    path: Path
    record: AuthorityClaimRecord


@dataclass(frozen=True)
class AuthorityReleaseEvidenceRecord:
    schema_version: int
    authority_key: str
    claim_id: str
    build_id: str
    execution_id: str
    terminal_status: str
    claim_sha256: str
    prepared_at: str
    release_condition: str = "verified_terminal_manifest"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class AuthorityReleaseEvidence:
    path: Path
    sha256: str
    record: AuthorityReleaseEvidenceRecord


def _record_from_payload(payload: object) -> AuthorityClaimRecord:
    if not isinstance(payload, dict):
        raise GlobalAuthorityError("Authority claim is invalid")
    required = {
        "schema_version",
        "authority_key",
        "claim_id",
        "build_id",
        "execution_id",
        "idempotency_key",
        "request_fingerprint",
        "normalized_target_root",
        "normalized_allowed_paths",
        "acquired_at",
        "status",
    }
    if set(payload) != required:
        raise GlobalAuthorityError("Authority claim fields are invalid")
    allowed = payload["normalized_allowed_paths"]
    if not isinstance(allowed, list) or not all(isinstance(path, str) for path in allowed):
        raise GlobalAuthorityError("Authority allowed paths are invalid")
    string_fields = required - {"schema_version", "normalized_allowed_paths"}
    if any(not isinstance(payload[name], str) for name in string_fields):
        raise GlobalAuthorityError("Authority claim field types are invalid")
    if payload["schema_version"] != 1 or payload["status"] != "active":
        raise GlobalAuthorityError("Authority claim version or status is invalid")
    record = AuthorityClaimRecord(
        schema_version=1,
        authority_key=payload["authority_key"],
        claim_id=payload["claim_id"],
        build_id=payload["build_id"],
        execution_id=payload["execution_id"],
        idempotency_key=payload["idempotency_key"],
        request_fingerprint=payload["request_fingerprint"],
        normalized_target_root=payload["normalized_target_root"],
        normalized_allowed_paths=tuple(allowed),
        acquired_at=payload["acquired_at"],
        status="active",
    )
    if not all(
        (record.claim_id, record.build_id, record.execution_id, record.idempotency_key)
    ):
        raise GlobalAuthorityError("Authority claim identity is invalid")
    _validate_sha256(record.authority_key, "authority_key")
    _validate_sha256(record.request_fingerprint, "request_fingerprint")
    normalized_target, normalized_allowed = normalized_authority_scope(
        record.normalized_target_root, record.normalized_allowed_paths
    )
    if (
        normalized_target != record.normalized_target_root
        or normalized_allowed != record.normalized_allowed_paths
    ):
        raise GlobalAuthorityError("Authority claim scope is not normalized")
    expected = derive_authority_key(normalized_target, normalized_allowed)
    if expected != record.authority_key:
        raise GlobalAuthorityError("Authority claim scope hash is invalid")
    return record


def _release_record_from_payload(payload: object) -> AuthorityReleaseEvidenceRecord:
    required = {
        "schema_version",
        "authority_key",
        "claim_id",
        "build_id",
        "execution_id",
        "terminal_status",
        "claim_sha256",
        "prepared_at",
        "release_condition",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise GlobalAuthorityError("Authority release evidence fields are invalid")
    if payload["schema_version"] != 1 or any(
        not isinstance(payload[name], str) for name in required - {"schema_version"}
    ):
        raise GlobalAuthorityError("Authority release evidence is invalid")
    if payload["terminal_status"] not in _TERMINAL_RELEASE_STATUSES:
        raise GlobalAuthorityError("Authority release terminal status is invalid")
    if payload["release_condition"] != "verified_terminal_manifest":
        raise GlobalAuthorityError("Authority release condition is invalid")
    _validate_sha256(payload["authority_key"], "authority_key")
    _validate_sha256(payload["claim_sha256"], "claim_sha256")
    return AuthorityReleaseEvidenceRecord(
        schema_version=1,
        authority_key=payload["authority_key"],
        claim_id=payload["claim_id"],
        build_id=payload["build_id"],
        execution_id=payload["execution_id"],
        terminal_status=payload["terminal_status"],
        claim_sha256=payload["claim_sha256"],
        prepared_at=payload["prepared_at"],
    )


class GlobalAuthorityRegistry:
    """Own active target-scope claims under one safe Audisor data root."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir.expanduser().resolve(strict=False)
        self.root = self.data_dir / "authority-locks"
        self.active_root = self.root / "active"
        self.history_root = self.root / "history"
        self.release_evidence_root = self.root / "release-evidence"
        self.mutex_root = self.root / ".mutex"

    def _paths(self, authority_key: str) -> tuple[Path, Path]:
        _validate_sha256(authority_key, "authority_key")
        return (
            self.active_root / f"{authority_key}.json",
            self.mutex_root / f"{authority_key}.lock",
        )

    @contextmanager
    def _locked(self, authority_key: str) -> Iterator[Path]:
        active_path, mutex_path = self._paths(authority_key)
        with _KeyMutex(mutex_path):
            yield active_path

    @staticmethod
    def _load_path(path: Path) -> AuthorityClaimRecord:
        if _is_reparse_or_symlink(path) or not path.is_file():
            raise GlobalAuthorityError("Authority claim path is unsafe")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise GlobalAuthorityError("Authority claim is unreadable") from None
        return _record_from_payload(payload)

    def load_active(self, authority_key: str) -> AuthorityClaimRecord | None:
        active_path, _mutex = self._paths(authority_key)
        if not active_path.exists() and not active_path.is_symlink():
            return None
        return self._load_path(active_path)

    def acquire(
        self,
        *,
        build_id: str,
        execution_id: str,
        idempotency_key: str,
        request_fingerprint: str,
        target_root: str | Path,
        allowed_paths: Sequence[str | Path],
    ) -> AuthorityClaim:
        """Atomically publish a durable claim before any execution side effect."""
        _validate_sha256(request_fingerprint, "request_fingerprint")
        target, allowed = normalized_authority_scope(target_root, allowed_paths)
        authority_key = derive_authority_key(target, allowed)
        record = AuthorityClaimRecord(
            schema_version=1,
            authority_key=authority_key,
            claim_id=str(uuid.uuid4()),
            build_id=build_id,
            execution_id=execution_id,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            normalized_target_root=target,
            normalized_allowed_paths=allowed,
            acquired_at=_utc_now(),
        )
        with self._locked(authority_key) as active_path:
            if active_path.exists() or active_path.is_symlink():
                raise GlobalAuthorityConflictError(self._load_path(active_path))
            try:
                _publish_no_replace(active_path, json_safe_bytes(record.to_dict(), indent=2))
            except FileExistsError:
                raise GlobalAuthorityConflictError(self._load_path(active_path)) from None
        return AuthorityClaim(path=active_path, record=record)

    def _history_path(self, record: AuthorityClaimRecord, outcome: str) -> Path:
        return self.history_root / record.authority_key / f"{record.claim_id}.{outcome}.json"

    def _release_evidence_path(self, record: AuthorityClaimRecord) -> Path:
        return self.release_evidence_root / record.authority_key / f"{record.claim_id}.json"

    @staticmethod
    def _read_complete_file(path: Path, description: str) -> bytes:
        if _is_reparse_or_symlink(path) or not path.is_file():
            raise GlobalAuthorityError(f"{description} path is unsafe")
        try:
            return path.read_bytes()
        except OSError:
            raise GlobalAuthorityError(f"{description} is unreadable") from None

    @staticmethod
    def _release_evidence_from_bytes(
        path: Path, content: bytes
    ) -> AuthorityReleaseEvidence:
        try:
            payload = json.loads(content.decode("utf-8", errors="strict"))
        except (UnicodeError, json.JSONDecodeError):
            raise GlobalAuthorityError("Authority release evidence is unreadable") from None
        return AuthorityReleaseEvidence(
            path=path,
            sha256=hashlib.sha256(content).hexdigest(),
            record=_release_record_from_payload(payload),
        )

    def prepare_release_evidence(
        self,
        claim: AuthorityClaim,
        *,
        terminal_status: str,
    ) -> AuthorityReleaseEvidence:
        """Persist immutable release evidence while retaining the active claim."""
        if terminal_status not in _TERMINAL_RELEASE_STATUSES:
            raise GlobalAuthorityError("Authority release terminal status is invalid")
        evidence_path = self._release_evidence_path(claim.record)
        with self._locked(claim.record.authority_key) as active_path:
            if not active_path.exists():
                raise GlobalAuthorityError("Active authority claim is missing")
            current = self._load_path(active_path)
            if current != claim.record:
                raise GlobalAuthorityError("Authority claim identity changed")
            claim_content = self._read_complete_file(active_path, "Authority claim")
            record = AuthorityReleaseEvidenceRecord(
                schema_version=1,
                authority_key=current.authority_key,
                claim_id=current.claim_id,
                build_id=current.build_id,
                execution_id=current.execution_id,
                terminal_status=terminal_status,
                claim_sha256=hashlib.sha256(claim_content).hexdigest(),
                prepared_at=_utc_now(),
            )
            if evidence_path.exists() or evidence_path.is_symlink():
                evidence = self._release_evidence_from_bytes(
                    evidence_path,
                    self._read_complete_file(evidence_path, "Authority release evidence"),
                )
                if (
                    evidence.record.authority_key != current.authority_key
                    or evidence.record.claim_id != current.claim_id
                    or evidence.record.build_id != current.build_id
                    or evidence.record.execution_id != current.execution_id
                    or evidence.record.terminal_status != terminal_status
                    or evidence.record.claim_sha256 != record.claim_sha256
                ):
                    raise GlobalAuthorityError("Authority release evidence conflicts")
                return evidence
            content = json_safe_bytes(record.to_dict(), indent=2)
            try:
                _publish_no_replace(evidence_path, content)
            except FileExistsError:
                content = self._read_complete_file(
                    evidence_path, "Authority release evidence"
                )
            return self._release_evidence_from_bytes(evidence_path, content)

    def _finalize_claim(
        self,
        claim: AuthorityClaim,
        *,
        outcome: str,
        evidence: dict[str, object],
    ) -> Path:
        history_path = self._history_path(claim.record, outcome)
        with self._locked(claim.record.authority_key) as active_path:
            if history_path.exists():
                if active_path.exists():
                    current = self._load_path(active_path)
                    if current.claim_id != claim.record.claim_id:
                        raise GlobalAuthorityError("Authority claim identity changed")
                    active_path.unlink()
                    _fsync_directory(active_path.parent)
                return history_path
            if not active_path.exists():
                raise GlobalAuthorityError("Active authority claim is missing")
            current = self._load_path(active_path)
            if current != claim.record:
                raise GlobalAuthorityError("Authority claim identity changed")
            history = {
                "schema_version": 1,
                "outcome": outcome,
                "claim": current.to_dict(),
                **evidence,
            }
            _publish_no_replace(history_path, json_safe_bytes(history, indent=2))
            active_path.unlink()
            _fsync_directory(active_path.parent)
        return history_path

    def release(
        self,
        claim: AuthorityClaim,
        *,
        terminal_status: str,
        terminal_manifest_sha256: str,
        release_evidence_sha256: str,
        reconciliation_verified: bool,
    ) -> Path:
        """Release only after a completed durable terminal reconciliation."""
        if not reconciliation_verified:
            raise GlobalAuthorityError("Authority release requires verified reconciliation")
        if terminal_status not in _TERMINAL_RELEASE_STATUSES:
            raise GlobalAuthorityError("Interrupted or invalid execution authority must be retained")
        _validate_sha256(terminal_manifest_sha256, "terminal_manifest_sha256")
        _validate_sha256(release_evidence_sha256, "release_evidence_sha256")
        release_evidence = self.prepare_release_evidence(
            claim, terminal_status=terminal_status
        )
        if release_evidence.sha256 != release_evidence_sha256:
            raise GlobalAuthorityError("Authority release evidence hash does not match")
        return self._finalize_claim(
            claim,
            outcome="released",
            evidence={
                "released_at": _utc_now(),
                "terminal_status": terminal_status,
                "terminal_manifest_sha256": terminal_manifest_sha256,
                "release_evidence_sha256": release_evidence.sha256,
                "claim_sha256": release_evidence.record.claim_sha256,
                "reconciliation_verified": True,
            },
        )

    def require_released_terminal_evidence(
        self,
        *,
        claim_evidence_path: Path,
        release_evidence_path: Path,
        terminal_manifest_sha256: str,
    ) -> None:
        """Verify local manifest-bound authority evidence against global history."""
        _validate_sha256(terminal_manifest_sha256, "terminal_manifest_sha256")
        claim_content = self._read_complete_file(
            claim_evidence_path, "Terminal authority claim evidence"
        )
        release_content = self._read_complete_file(
            release_evidence_path, "Terminal authority release evidence"
        )
        try:
            claim_payload = json.loads(claim_content.decode("utf-8", errors="strict"))
        except (UnicodeError, json.JSONDecodeError):
            raise GlobalAuthorityError("Terminal authority claim evidence is invalid") from None
        claim = _record_from_payload(claim_payload)
        release = self._release_evidence_from_bytes(
            release_evidence_path, release_content
        )
        if (
            release.record.authority_key != claim.authority_key
            or release.record.claim_id != claim.claim_id
            or release.record.build_id != claim.build_id
            or release.record.execution_id != claim.execution_id
            or release.record.claim_sha256
            != hashlib.sha256(claim_content).hexdigest()
        ):
            raise GlobalAuthorityError("Terminal authority evidence identity is invalid")
        global_release_content = self._read_complete_file(
            self._release_evidence_path(claim),
            "Global authority release evidence",
        )
        if global_release_content != release_content:
            raise GlobalAuthorityError("Global authority release evidence does not match")
        history_path = self._history_path(claim, "released")
        history_content = self._read_complete_file(
            history_path, "Global authority release history"
        )
        try:
            history = json.loads(history_content.decode("utf-8", errors="strict"))
        except (UnicodeError, json.JSONDecodeError):
            raise GlobalAuthorityError("Global authority release history is invalid") from None
        if not isinstance(history, dict) or (
            history.get("schema_version") != 1
            or history.get("outcome") != "released"
            or history.get("claim") != claim.to_dict()
            or history.get("terminal_status") != release.record.terminal_status
            or history.get("terminal_manifest_sha256") != terminal_manifest_sha256
            or history.get("release_evidence_sha256") != release.sha256
            or history.get("claim_sha256") != release.record.claim_sha256
            or history.get("reconciliation_verified") is not True
        ):
            raise GlobalAuthorityError("Global authority release history does not match")

    def recover(
        self,
        *,
        authority_key: str,
        expected_claim_id: str,
        recovery_evidence_sha256: str,
        safe_to_release: bool,
        reason: str,
    ) -> Path:
        """Explicitly release an interrupted claim after evidence-based recovery."""
        if not safe_to_release:
            raise GlobalAuthorityError("Recovery did not establish safe release")
        if not isinstance(reason, str) or not reason.strip():
            raise GlobalAuthorityError("Recovery requires a reason")
        _validate_sha256(recovery_evidence_sha256, "recovery_evidence_sha256")
        active = self.load_active(authority_key)
        if active is None:
            raise GlobalAuthorityError("Active authority claim is missing")
        if active.claim_id != expected_claim_id:
            raise GlobalAuthorityError("Authority recovery identity does not match")
        claim = AuthorityClaim(path=self._paths(authority_key)[0], record=active)
        return self._finalize_claim(
            claim,
            outcome="recovered",
            evidence={
                "recovered_at": _utc_now(),
                "recovery_evidence_sha256": recovery_evidence_sha256,
                "safe_to_release": True,
                "reason": reason.strip(),
            },
        )
