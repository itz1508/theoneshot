from __future__ import annotations

import hashlib
import json
import tomllib
from pathlib import Path

from jsonschema import Draft202012Validator

from aflow.schemas.registry import SCHEMA_ROOT, registry, schemas


def test_all_authoritative_schemas_are_byte_locked():
    expected = json.loads((SCHEMA_ROOT / "SHA256SUMS.json").read_text(encoding="utf-8"))
    actual = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(SCHEMA_ROOT.glob("*.schema.json"))
    }
    assert len(actual) == 23
    assert actual == expected


def test_all_schemas_are_meta_valid_and_refs_resolve():
    loaded = schemas()
    assert len(loaded) == 23
    for value in loaded.values():
        Draft202012Validator.check_schema(value)
        validator = Draft202012Validator(value, registry=registry())
        for reference in _refs(value):
            if reference.startswith("http"):
                registry().resolver().lookup(reference)
            else:
                registry().resolver(value["$id"]).lookup(reference)
        assert validator.schema is value


def test_wheel_configuration_force_includes_versioned_schemas():
    project = tomllib.loads((Path(__file__).parents[2] / "pyproject.toml").read_text(encoding="utf-8"))
    force = project["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    assert force == {"schemas/v1": "aflow/schemas/v1"}


def _refs(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "$ref":
                yield item
            else:
                yield from _refs(item)
    elif isinstance(value, list):
        for item in value:
            yield from _refs(item)
