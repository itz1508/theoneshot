"""Worker-output processing: JSON extraction and recorded, removal-only repair."""

from __future__ import annotations

import json
import re
from typing import Any, Mapping

from .stage_contracts import _OPTIONAL_STRING_FIELDS, StageOutputError


def _parse_json_object(text: str) -> Mapping[str, Any]:
    """Extract the JSON object from a model answer (tolerates code fences)."""
    candidate = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", candidate, re.DOTALL)
    if fenced:
        candidate = fenced.group(1)
    else:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start >= 0 and end > start:
            candidate = candidate[start : end + 1]
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise StageOutputError(f"worker answer is not valid JSON: {exc}") from None
    if not isinstance(parsed, dict):
        raise StageOutputError("worker answer must be a JSON object")
    return parsed


def _prune_to_schema(
    schema: Mapping[str, Any], value: Any, path: str = "$", removed: list[dict[str, Any]] | None = None
) -> tuple[Any, list[dict[str, Any]]]:
    """Remove keys beyond the stage schema's properties, recording every removal.

    Deliberate, auditable repair: each removed field is recorded with its path
    and original value so non-conformance evidence is never erased. Required
    fields and types are untouched — validation still decides every transition,
    and repair can never fabricate a missing required field.
    """
    if removed is None:
        removed = []
    if schema.get("type") == "object" and isinstance(value, dict):
        properties = schema.get("properties", {})
        kept: dict[str, Any] = {}
        for key, item in value.items():
            if key in properties:
                kept[key], _ = _prune_to_schema(properties[key], item, f"{path}.{key}", removed)
            else:
                removed.append({"path": f"{path}.{key}", "removed": item})
        return kept, removed
    if schema.get("type") == "array" and isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            return (
                [
                    _prune_to_schema(item_schema, item, f"{path}[{index}]", removed)[0]
                    for index, item in enumerate(value)
                ],
                removed,
            )
    return value, removed


def _drop_empty_optionals(stage_name: str, parsed: Mapping[str, Any]) -> Mapping[str, Any]:
    """Remove optional string fields a model emitted as empty/blank strings."""
    optional_map = _OPTIONAL_STRING_FIELDS.get(stage_name)
    if not optional_map or not isinstance(parsed, dict):
        return parsed
    cleaned = dict(parsed)
    for list_key, optional_fields in optional_map.items():
        items = cleaned.get(list_key)
        if not isinstance(items, list):
            continue
        cleaned[list_key] = [
            {
                key: value
                for key, value in item.items()
                if not (key in optional_fields and isinstance(value, str) and not value.strip())
            }
            if isinstance(item, dict)
            else item
            for item in items
        ]
    return cleaned
