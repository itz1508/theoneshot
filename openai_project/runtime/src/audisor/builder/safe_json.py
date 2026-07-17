"""Fail-safe recursive normalization for durable JSON evidence."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any


def unicode_safe_text(value: object) -> str:
    """Return UTF-8-encodable text while preserving malformed code points visibly."""
    try:
        text = value if isinstance(value, str) else str(value)
    except Exception as error:  # pragma: no cover - defensive fallback
        text = f"<unprintable {type(value).__name__}: {type(error).__name__}>"
    return text.encode("utf-8", errors="backslashreplace").decode("utf-8")


def _normalized_sort_key(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def normalize_json_safe(value: object, *, max_depth: int = 64) -> Any:
    """Recursively convert arbitrary evidence into deterministic JSON-safe values.

    Lone surrogates become visible ``\\udxxx`` text, invalid bytes use escaped byte
    notation, cycles are marked, non-finite floats become strings, and unknown
    objects fall back to a safe textual representation. The result can always be
    serialized as strict UTF-8 JSON unless ``max_depth`` itself is invalid.
    """
    if max_depth < 1:
        raise ValueError("max_depth must be positive")

    active: set[int] = set()

    def visit(item: object, depth: int) -> Any:
        if depth > max_depth:
            return "<maximum depth exceeded>"
        if item is None or isinstance(item, (bool, int)):
            return item
        if isinstance(item, float):
            if math.isfinite(item):
                return item
            if math.isnan(item):
                return "NaN"
            return "Infinity" if item > 0 else "-Infinity"
        if isinstance(item, str):
            return unicode_safe_text(item)
        if isinstance(item, bytes):
            return item.decode("utf-8", errors="backslashreplace")
        if isinstance(item, (Path, date, datetime)):
            return unicode_safe_text(item)
        if isinstance(item, Enum):
            return visit(item.value, depth + 1)
        if isinstance(item, BaseException):
            return {
                "type": type(item).__name__,
                "message": unicode_safe_text(item),
            }

        identity = id(item)
        if identity in active:
            return "<circular reference>"

        if is_dataclass(item) and not isinstance(item, type):
            active.add(identity)
            try:
                return visit(asdict(item), depth + 1)
            except Exception:
                return unicode_safe_text(item)
            finally:
                active.remove(identity)

        model_dump = getattr(item, "model_dump", None)
        if callable(model_dump):
            active.add(identity)
            try:
                return visit(model_dump(mode="python"), depth + 1)
            except Exception:
                return unicode_safe_text(item)
            finally:
                active.remove(identity)

        if isinstance(item, Mapping):
            active.add(identity)
            try:
                normalized: dict[str, Any] = {}
                for key, child in item.items():
                    normalized_key = unicode_safe_text(key)
                    candidate = normalized_key
                    suffix = 2
                    while candidate in normalized:
                        candidate = f"{normalized_key}#{suffix}"
                        suffix += 1
                    normalized[candidate] = visit(child, depth + 1)
                return normalized
            finally:
                active.remove(identity)

        if isinstance(item, (list, tuple)):
            active.add(identity)
            try:
                return [visit(child, depth + 1) for child in item]
            finally:
                active.remove(identity)

        if isinstance(item, (set, frozenset)):
            active.add(identity)
            try:
                children = [visit(child, depth + 1) for child in item]
                return sorted(children, key=_normalized_sort_key)
            finally:
                active.remove(identity)

        return unicode_safe_text(item)

    return visit(value, 0)


def json_safe_dumps(
    value: object,
    *,
    indent: int | None = None,
    sort_keys: bool = True,
) -> str:
    """Serialize normalized evidence without allowing invalid JSON numbers."""
    return json.dumps(
        normalize_json_safe(value),
        ensure_ascii=True,
        sort_keys=sort_keys,
        separators=(",", ":") if indent is None else None,
        indent=indent,
        allow_nan=False,
    )


def json_safe_bytes(
    value: object,
    *,
    indent: int | None = None,
    sort_keys: bool = True,
    trailing_newline: bool = True,
) -> bytes:
    """Return durable UTF-8 JSON bytes for arbitrary evidence."""
    text = json_safe_dumps(value, indent=indent, sort_keys=sort_keys)
    if trailing_newline:
        text += "\n"
    return text.encode("utf-8", errors="strict")
