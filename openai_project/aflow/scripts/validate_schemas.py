from __future__ import annotations

from jsonschema import Draft202012Validator

from aflow.schemas.registry import registry, schemas


if __name__ == "__main__":
    count = 0
    for name, value in schemas().items():
        Draft202012Validator.check_schema(value)
        Draft202012Validator(value, registry=registry())
        print(f"PASS {name}")
        count += 1
    print(f"schemas={count}")

