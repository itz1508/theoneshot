from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from referencing import Registry, Resource


PACKAGE_SCHEMA_ROOT = Path(__file__).resolve().parent / "v1"
CHECKOUT_SCHEMA_ROOT = Path(__file__).resolve().parents[3] / "schemas" / "v1"
SCHEMA_ROOT = PACKAGE_SCHEMA_ROOT if PACKAGE_SCHEMA_ROOT.is_dir() else CHECKOUT_SCHEMA_ROOT


@lru_cache(maxsize=1)
def schemas() -> dict[str, dict[str, Any]]:
    return {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(SCHEMA_ROOT.glob("*.schema.json"))
    }


@lru_cache(maxsize=1)
def registry() -> Registry:
    result = Registry()
    for name, schema in schemas().items():
        resource = Resource.from_contents(schema)
        result = result.with_resource(schema["$id"], resource)
        result = result.with_resource(name, resource)
    return result


def schema(name: str) -> dict[str, Any]:
    try:
        return schemas()[name]
    except KeyError as exc:
        raise KeyError(f"unknown A-Flow schema: {name}") from exc
