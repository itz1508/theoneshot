from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any


HASH_FIELDS = {"content_hash", "lock_hash"}


def canonical_bytes(value: Any, *, omit_fields: set[str] | None = None) -> bytes:
    omitted = HASH_FIELDS if omit_fields is None else omit_fields

    def copy_value(item: Any) -> Any:
        if isinstance(item, dict):
            return {key: copy_value(val) for key, val in sorted(item.items())}
        if isinstance(item, list):
            return [copy_value(val) for val in item]
        return item

    scrubbed = copy_value(value)
    if isinstance(scrubbed, dict):
        for field in omitted:
            scrubbed.pop(field, None)
        # success-definition.schema.json places the artifact's own hash here.
        # All other nested content hashes are references and remain bound.
        if "success_definition_id" in scrubbed and isinstance(scrubbed.get("confirmation"), dict):
            scrubbed["confirmation"].pop("content_hash", None)

    return json.dumps(
        scrubbed, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def canonical_hash(value: Any, *, omit_fields: set[str] | None = None) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value, omit_fields=omit_fields)).hexdigest()


def seal(value: dict[str, Any], field: str = "content_hash") -> dict[str, Any]:
    result = deepcopy(value)
    result[field] = canonical_hash(result)
    return result


def verify_hash(value: dict[str, Any], field: str = "content_hash") -> bool:
    return value.get(field) == canonical_hash(value)


def artifact_ref(
    artifact: dict[str, Any], schema_name: str, *, id_field: str, version_field: str = "version"
) -> dict[str, str]:
    version = artifact.get(version_field, artifact.get("schema_version", "1.0.0"))
    content_hash = artifact.get("content_hash") or artifact.get("lock_hash") or canonical_hash(artifact)
    return {
        "artifact_id": artifact[id_field],
        "schema_id": f"https://theoneshot.dev/schemas/aflow/v1/{schema_name}",
        "version": version,
        "content_hash": content_hash,
    }
