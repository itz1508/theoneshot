"""Durable data-root idempotency lookup that precedes external resolution."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from audisor.builder.safe_json import json_safe_bytes

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class IdempotencyIndexError(RuntimeError):
    """The durable idempotency index is unsafe or corrupt."""


class IdempotencyConflictError(IdempotencyIndexError):
    """An idempotency key is already bound to a different request."""

    def __init__(self, record: "IdempotencyRecord") -> None:
        super().__init__("Idempotency key is already bound to different input")
        self.record = record


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_idempotency_key(value: str) -> str:
    """Normalize a caller key for stable hashing without changing case semantics."""
    if not isinstance(value, str):
        raise IdempotencyIndexError("Idempotency key must be a string")
    normalized = unicodedata.normalize("NFKC", value)
    if (
        not normalized
        or normalized != normalized.strip()
        or "\x00" in normalized
        or len(normalized) > 512
    ):
        raise IdempotencyIndexError("Idempotency key is invalid")
    try:
        normalized.encode("utf-8", errors="strict")
    except UnicodeError:
        raise IdempotencyIndexError("Idempotency key contains invalid Unicode") from None
    return normalized


def derive_idempotency_key_hash(value: str) -> str:
    normalized = normalize_idempotency_key(value)
    return hashlib.sha256(normalized.encode("utf-8", errors="strict")).hexdigest()


def fingerprint_request(value: object) -> str:
    """Hash canonical normalized request data independently of current target state."""
    return hashlib.sha256(json_safe_bytes(value, trailing_newline=False)).hexdigest()


def _validate_sha256(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise IdempotencyIndexError(f"{field_name} must be a lowercase SHA-256 digest")
    return value


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
        raise IdempotencyIndexError("Idempotency index directory is unsafe")


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


@dataclass(frozen=True)
class IdempotencyRecord:
    schema_version: int
    key_hash: str
    request_fingerprint: str
    build_id: str
    execution_id: str
    execution_path: str
    state_path: str
    created_at: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class IdempotencyBinding:
    record: IdempotencyRecord
    is_new: bool


def _record_from_payload(payload: object) -> IdempotencyRecord:
    required = {
        "schema_version",
        "key_hash",
        "request_fingerprint",
        "build_id",
        "execution_id",
        "execution_path",
        "state_path",
        "created_at",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise IdempotencyIndexError("Idempotency record fields are invalid")
    if any(not isinstance(payload[name], str) for name in required - {"schema_version"}):
        raise IdempotencyIndexError("Idempotency record field types are invalid")
    if payload["schema_version"] != 1:
        raise IdempotencyIndexError("Idempotency record version is invalid")
    record = IdempotencyRecord(
        schema_version=1,
        key_hash=payload["key_hash"],
        request_fingerprint=payload["request_fingerprint"],
        build_id=payload["build_id"],
        execution_id=payload["execution_id"],
        execution_path=payload["execution_path"],
        state_path=payload["state_path"],
        created_at=payload["created_at"],
    )
    _validate_sha256(record.key_hash, "key_hash")
    _validate_sha256(record.request_fingerprint, "request_fingerprint")
    if not record.build_id or not record.execution_id:
        raise IdempotencyIndexError("Idempotency execution identity is invalid")
    execution = Path(record.execution_path)
    state = Path(record.state_path)
    if not execution.is_absolute() or not state.is_absolute():
        raise IdempotencyIndexError("Idempotency execution references are invalid")
    try:
        if os.path.commonpath(
            [os.path.normcase(str(state)), os.path.normcase(str(execution))]
        ) != os.path.normcase(str(execution)):
            raise IdempotencyIndexError("Idempotency state reference escapes execution")
    except ValueError:
        raise IdempotencyIndexError("Idempotency state reference escapes execution") from None
    return record


class IdempotencyIndex:
    """Immutable key-to-execution references stored outside individual builds."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir.expanduser().resolve(strict=False)
        self.root = self.data_dir / "idempotency"

    def _path(self, key_hash: str) -> Path:
        _validate_sha256(key_hash, "key_hash")
        return self.root / f"{key_hash}.json"

    @staticmethod
    def _load_path(path: Path) -> IdempotencyRecord:
        if _is_reparse_or_symlink(path) or not path.is_file():
            raise IdempotencyIndexError("Idempotency record path is unsafe")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise IdempotencyIndexError("Idempotency record is unreadable") from None
        return _record_from_payload(payload)

    def lookup(
        self,
        idempotency_key: str,
        *,
        request_fingerprint: str | None = None,
    ) -> IdempotencyRecord | None:
        """Return a stored reference without resolving target, plan, or worker state."""
        key_hash = derive_idempotency_key_hash(idempotency_key)
        path = self._path(key_hash)
        if self.root.exists() and (
            not self.root.is_dir() or _is_reparse_or_symlink(self.root)
        ):
            raise IdempotencyIndexError("Idempotency index directory is unsafe")
        if not path.exists() and not path.is_symlink():
            return None
        record = self._load_path(path)
        if record.key_hash != key_hash:
            raise IdempotencyIndexError("Idempotency record key hash is invalid")
        if request_fingerprint is not None:
            _validate_sha256(request_fingerprint, "request_fingerprint")
            if record.request_fingerprint != request_fingerprint:
                raise IdempotencyConflictError(record)
        return record

    def lookup_before_resolution(
        self,
        idempotency_key: str,
        request_payload: object,
    ) -> IdempotencyRecord | None:
        """Canonical request-handler first step for replay/conflict decisions."""
        return self.lookup(
            idempotency_key,
            request_fingerprint=fingerprint_request(request_payload),
        )

    def bind(
        self,
        *,
        idempotency_key: str,
        request_fingerprint: str,
        build_id: str,
        execution_id: str,
        execution_path: str | Path,
        state_path: str | Path | None = None,
    ) -> IdempotencyBinding:
        """Atomically bind a new key, or return the identical durable binding."""
        key_hash = derive_idempotency_key_hash(idempotency_key)
        _validate_sha256(request_fingerprint, "request_fingerprint")
        execution = Path(execution_path).expanduser()
        if not execution.is_absolute():
            raise IdempotencyIndexError("Execution path must be absolute")
        execution = execution.resolve(strict=False)
        state = (
            Path(state_path).expanduser()
            if state_path is not None
            else execution / "state.json"
        )
        if not state.is_absolute():
            raise IdempotencyIndexError("State path must be absolute")
        state = state.resolve(strict=False)
        try:
            if os.path.commonpath(
                [os.path.normcase(str(state)), os.path.normcase(str(execution))]
            ) != os.path.normcase(str(execution)):
                raise IdempotencyIndexError("State path must be inside execution path")
        except ValueError:
            raise IdempotencyIndexError("State path must be inside execution path") from None
        record = IdempotencyRecord(
            schema_version=1,
            key_hash=key_hash,
            request_fingerprint=request_fingerprint,
            build_id=build_id,
            execution_id=execution_id,
            execution_path=str(execution),
            state_path=str(state),
            created_at=_utc_now(),
        )
        path = self._path(key_hash)
        existing = self.lookup(
            idempotency_key, request_fingerprint=request_fingerprint
        )
        if existing is not None:
            return IdempotencyBinding(record=existing, is_new=False)
        try:
            _publish_no_replace(path, json_safe_bytes(record.to_dict(), indent=2))
            return IdempotencyBinding(record=record, is_new=True)
        except FileExistsError:
            existing = self.lookup(
                idempotency_key, request_fingerprint=request_fingerprint
            )
            if existing is None:  # pragma: no cover - defensive filesystem race
                raise IdempotencyIndexError("Idempotency publication was lost") from None
            return IdempotencyBinding(record=existing, is_new=False)
