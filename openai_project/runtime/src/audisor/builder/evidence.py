"""Bounded sanitization, hashing, and atomic durable-file helpers."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from audisor.builder.safe_json import json_safe_bytes

SENSITIVE_ENV_NAME_RE = re.compile(
    r"(?:api[_-]?key|token|secret|password|credential|authorization)", re.I
)
TOKEN_SHAPE_RE = re.compile(r"\b(?:sk|fw|key|token)-[A-Za-z0-9_-]{8,}\b", re.I)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    return json_safe_bytes(value, indent=None, sort_keys=True)


def sensitive_values() -> tuple[str, ...]:
    """Return nontrivial secret-shaped environment values without exposing names."""
    values = {
        value
        for name, value in os.environ.items()
        if SENSITIVE_ENV_NAME_RE.search(name) and len(value) >= 8
    }
    return tuple(sorted(values, key=len, reverse=True))


def contains_environment_secret(value: str) -> bool:
    return any(secret in value for secret in sensitive_values())


def sanitize_text(value: object, *, limit: int = 8192) -> tuple[str, bool]:
    """Redact known environment secrets and bound persisted/public excerpts."""
    text = value if isinstance(value, str) else str(value)
    for secret in sensitive_values():
        text = text.replace(secret, "[REDACTED]")
    text = TOKEN_SHAPE_RE.sub("[REDACTED]", text)
    # Backslash-escape lone surrogates and other unencodable text before any
    # durable evidence path attempts UTF-8 serialization.
    text = text.encode("utf-8", errors="backslashreplace").decode("utf-8")
    truncated = len(text.encode("utf-8")) > limit
    if truncated:
        encoded = text.encode("utf-8")[:limit]
        text = encoded.decode("utf-8", errors="ignore") + "…[truncated]"
    return text, truncated


def atomic_write_bytes(path: Path, content: bytes) -> None:
    """Flush and atomically replace one durable file with a same-directory temp."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
        except (OSError, AttributeError):
            return
        try:
            os.fsync(directory_fd)
        except OSError:
            pass
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_json(path: Path, value: object) -> bytes:
    content = json_safe_bytes(value, indent=2, sort_keys=True)
    atomic_write_bytes(path, content)
    return content
