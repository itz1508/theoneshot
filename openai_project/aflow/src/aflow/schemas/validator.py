from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

from .registry import registry, schema


def json_pointer(parts: Iterable[object]) -> str:
    escaped = [str(part).replace("~", "~0").replace("/", "~1") for part in parts]
    return "" if not escaped else "/" + "/".join(escaped)


@dataclass(frozen=True)
class ValidationIssue:
    keyword: str
    instance_path: str
    schema_path: str
    message: str


class SchemaValidationError(ValueError):
    def __init__(self, schema_name: str, issues: list[ValidationIssue]):
        self.schema_name = schema_name
        self.issues = issues
        summary = "; ".join(f"{issue.instance_path or '/'}: {issue.message}" for issue in issues)
        super().__init__(f"{schema_name} validation failed: {summary}")


def issues_for(value: Any, schema_name: str) -> list[ValidationIssue]:
    validator = Draft202012Validator(
        schema(schema_name), registry=registry(), format_checker=FormatChecker()
    )
    errors: list[ValidationError] = sorted(
        validator.iter_errors(value), key=lambda item: (list(item.absolute_path), item.message)
    )
    return [
        ValidationIssue(
            keyword=str(error.validator),
            instance_path=json_pointer(error.absolute_path),
            schema_path=json_pointer(error.absolute_schema_path),
            message=error.message,
        )
        for error in errors
    ]


def validate(value: Any, schema_name: str) -> Any:
    issues = issues_for(value, schema_name)
    if issues:
        raise SchemaValidationError(schema_name, issues)
    return value

