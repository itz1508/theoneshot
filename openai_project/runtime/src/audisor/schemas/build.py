"""Builder request, plan, task, and generated-skill schemas."""

from __future__ import annotations

import re
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SAFE_IDENTIFIER_PATTERN = r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,62}[A-Za-z0-9])?$"
SAFE_IDENTIFIER_RE = re.compile(SAFE_IDENTIFIER_PATTERN)
WINDOWS_RESERVED_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}
PLACEHOLDER_VALUES = {
    "...",
    "n/a",
    "none",
    "not specified",
    "placeholder",
    "tbd",
    "todo",
    "unknown",
}

SafeIdentifier = Annotated[
    str,
    Field(strict=True, min_length=1, max_length=64, pattern=SAFE_IDENTIFIER_PATTERN),
]


def validate_safe_identifier(value: str, field_name: str = "identifier") -> str:
    """Reject traversal, absolute paths, unsafe names, and Windows devices."""
    if not SAFE_IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be a safe identifier")
    device_name = value.split(".", 1)[0].casefold()
    if device_name in WINDOWS_RESERVED_NAMES:
        raise ValueError(f"{field_name} uses a reserved Windows name")
    return value


def _require_content(value: str, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _is_placeholder(value: str) -> bool:
    return value.strip().casefold() in PLACEHOLDER_VALUES


def validate_task_relative_path(value: str, field_name: str) -> str:
    """Reject host, traversal, and Windows-device paths in prepared tasks."""
    if not value or value != value.strip() or "\x00" in value:
        raise ValueError(f"{field_name} must be a safe relative path")
    if value.startswith(("\\\\", "//", "\\?\\", "\\.\\")):
        raise ValueError(f"{field_name} must be a safe relative path")
    windows = PureWindowsPath(value)
    posix = PurePosixPath(value.replace("\\", "/"))
    if windows.drive or windows.root or posix.is_absolute() or ":" in value:
        raise ValueError(f"{field_name} must be a safe relative path")
    parts = posix.parts
    if not parts or any(part in {".", ".."} for part in parts):
        raise ValueError(f"{field_name} must not traverse")
    for part in parts:
        if part.endswith((".", " ")):
            raise ValueError(f"{field_name} contains an unsafe path component")
        if part.split(".", 1)[0].casefold() in WINDOWS_RESERVED_NAMES:
            raise ValueError(f"{field_name} contains a reserved Windows name")
    return value


class TaskValidationCommand(BaseModel):
    """Deferred executable validation metadata retained for a future phase."""

    model_config = ConfigDict(extra="forbid", strict=True)

    argv: list[Annotated[str, Field(strict=True, min_length=1, max_length=4096)]] = Field(
        min_length=1, max_length=128
    )
    working_directory: Annotated[str, Field(strict=True, min_length=1, max_length=4096)] = "."
    acceptable_exit_codes: list[Annotated[int, Field(strict=True, ge=0, le=255)]] = Field(
        default_factory=lambda: [0], min_length=1, max_length=16
    )
    timeout_seconds: Annotated[int, Field(strict=True, ge=1, le=300)] = 60

    @field_validator("argv")
    @classmethod
    def validate_argv(cls, values: list[str]) -> list[str]:
        if any("\x00" in value for value in values):
            raise ValueError("validation command arguments must not contain NUL")
        return values

    @field_validator("working_directory")
    @classmethod
    def validate_working_directory(cls, value: str) -> str:
        if value == ".":
            return value
        return validate_task_relative_path(value, "working_directory")

    @field_validator("acceptable_exit_codes")
    @classmethod
    def validate_exit_codes(cls, values: list[int]) -> list[int]:
        if len(values) != len(set(values)):
            raise ValueError("acceptable_exit_codes must be unique")
        return values


class BuildRequest(BaseModel):
    """One build instruction accepted by the preparation API."""

    model_config = ConfigDict(extra="forbid", strict=True)

    build_id: SafeIdentifier
    instruction: Annotated[str, Field(strict=True, max_length=100_000)]

    @field_validator("build_id")
    @classmethod
    def validate_build_id(cls, value: str) -> str:
        return validate_safe_identifier(value, "build_id")

    @field_validator("instruction")
    @classmethod
    def validate_instruction(cls, value: str) -> str:
        return _require_content(value, "instruction")


class BuildTask(BaseModel):
    """One dependency-aware task returned by the planning worker."""

    model_config = ConfigDict(extra="forbid", strict=True)

    task_id: SafeIdentifier
    title: Annotated[str, Field(strict=True, max_length=200)]
    depends_on: list[SafeIdentifier] = Field(max_length=256)
    prompt: Annotated[str, Field(strict=True, max_length=100_000)]
    expected_outputs: list[Annotated[str, Field(strict=True, min_length=1, max_length=4096)]] = Field(
        min_length=1, max_length=256
    )
    validation: list[TaskValidationCommand] = Field(min_length=1, max_length=32)

    @field_validator("task_id")
    @classmethod
    def validate_task_id(cls, value: str) -> str:
        return validate_safe_identifier(value, "task_id")

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        normalized = _require_content(value, "title").strip()
        if "\n" in normalized or "\r" in normalized:
            raise ValueError("title must be a single line")
        if _is_placeholder(normalized):
            raise ValueError("title must not be a placeholder")
        return normalized

    @field_validator("prompt")
    @classmethod
    def validate_prompt(cls, value: str) -> str:
        normalized = _require_content(value, "prompt")
        normalized = normalized.replace("\r\n", "\n").replace("\r", "\n").strip()
        if _is_placeholder(normalized):
            raise ValueError("prompt must not be a placeholder")
        return normalized

    @field_validator("expected_outputs")
    @classmethod
    def validate_expected_outputs(cls, values: list[str]) -> list[str]:
        seen: set[str] = set()
        for value in values:
            validate_task_relative_path(value, "expected_outputs")
            key = value.replace("\\", "/").casefold()
            if key in seen:
                raise ValueError("expected_outputs must be unique")
            seen.add(key)
        return values


class BuildPlan(BaseModel):
    """Normalized ready or blocked plan returned by Phase 2A."""

    model_config = ConfigDict(extra="forbid", strict=True)

    build_id: SafeIdentifier
    status: Literal["ready", "blocked"]
    gaps: list[Annotated[str, Field(strict=True, max_length=1000)]] = Field(max_length=100)
    tasks: list[BuildTask] = Field(max_length=256)

    @field_validator("build_id")
    @classmethod
    def validate_build_id(cls, value: str) -> str:
        return validate_safe_identifier(value, "build_id")

    @field_validator("gaps")
    @classmethod
    def validate_gaps(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            gap = _require_content(value, "gap").strip()
            if _is_placeholder(gap):
                raise ValueError("gaps must contain specific missing information")
            key = gap.casefold()
            if key in seen:
                raise ValueError("gaps must be unique")
            seen.add(key)
            normalized.append(gap)
        return normalized

    @model_validator(mode="after")
    def validate_status_shape(self) -> Self:
        if self.status == "ready":
            if self.gaps:
                raise ValueError("ready plans must not contain unresolved gaps")
            if not self.tasks:
                raise ValueError("ready plans must contain at least one task")
        else:
            if not self.gaps:
                raise ValueError("blocked plans must contain at least one gap")
            if self.tasks:
                raise ValueError("blocked plans must not contain tasks")
        return self


class TaskSkill(BaseModel):
    """Exact AMD-compatible mapping for one generated SKILL.md."""

    model_config = ConfigDict(extra="forbid", strict=True)

    task_id: SafeIdentifier
    prompt: Annotated[str, Field(strict=True, max_length=200_000)]

    @field_validator("task_id")
    @classmethod
    def validate_task_id(cls, value: str) -> str:
        return validate_safe_identifier(value, "task_id")

    @field_validator("prompt")
    @classmethod
    def validate_prompt(cls, value: str) -> str:
        return _require_content(value, "prompt")
