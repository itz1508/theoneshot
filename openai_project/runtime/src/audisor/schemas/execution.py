"""Strict Phase 2B execution, action, result, and evidence schemas."""

from __future__ import annotations

import re
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Any, Literal, Self, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from audisor.schemas.build import SafeIdentifier, validate_safe_identifier

TaskExecutionStatus = Literal[
    "pending",
    "ready",
    "running",
    "completed",
    "failed",
    "blocked",
    "interrupted",
]
ExecutionStatus = Literal["running", "completed", "failed", "interrupted", "not_valid"]

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
WINDOWS_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}


def validate_relative_path(value: str, *, allow_dot: bool = False) -> str:
    """Validate a platform-neutral relative path before filesystem resolution."""
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("path must be a non-empty unpadded string")
    if "\x00" in value or value.startswith(("\\\\", "//", "\\?\\", "\\.\\")):
        raise ValueError("path must be a safe relative path")
    if allow_dot and value == ".":
        return value
    windows = PureWindowsPath(value)
    posix = PurePosixPath(value.replace("\\", "/"))
    if windows.drive or windows.root or posix.is_absolute() or ":" in value:
        raise ValueError("path must be a safe relative path")
    parts = [part for part in posix.parts if part not in {""}]
    if not parts or any(part in {".", ".."} for part in parts):
        raise ValueError("path must not traverse")
    for part in parts:
        if part.endswith((".", " ")):
            raise ValueError("path components must not end with dot or space")
        device = part.split(".", 1)[0].casefold()
        if device in WINDOWS_RESERVED:
            raise ValueError("path contains a reserved Windows name")
    return value


class BuildExecutionRequest(BaseModel):
    """Explicit authority requested for one prepared-build execution."""

    model_config = ConfigDict(extra="forbid", strict=True)

    execution_id: SafeIdentifier
    idempotency_key: SafeIdentifier
    target_root: Annotated[str, Field(strict=True, min_length=1, max_length=32_767)]
    allowed_write_paths: list[
        Annotated[str, Field(strict=True, min_length=1, max_length=4096)]
    ] = Field(min_length=1, max_length=64)
    # Exact frozen A-Flow analysis-request document supplied by the accepted
    # operation. It is validated by the host package assembler, not here.
    aflow_analysis_request: dict[str, Any] | None = None

    @field_validator("execution_id", "idempotency_key")
    @classmethod
    def validate_ids(cls, value: str, info) -> str:
        return validate_safe_identifier(value, info.field_name)

    @field_validator("target_root")
    @classmethod
    def validate_target_root(cls, value: str) -> str:
        if not value.strip() or "\x00" in value:
            raise ValueError("target_root must be a non-empty path")
        return value

    @field_validator("allowed_write_paths")
    @classmethod
    def validate_allowed_paths(cls, values: list[str]) -> list[str]:
        seen: set[str] = set()
        for value in values:
            validate_relative_path(value)
            key = value.replace("\\", "/").casefold()
            if key in seen:
                raise ValueError("allowed_write_paths must be unique")
            seen.add(key)
        return values


class TaskStateRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    task_id: SafeIdentifier
    status: TaskExecutionStatus

    @field_validator("task_id")
    @classmethod
    def validate_task_id(cls, value: str) -> str:
        return validate_safe_identifier(value, "task_id")


class BuildExecutionState(BaseModel):
    """Public and durable execution state, kept in prepared-plan order."""

    model_config = ConfigDict(extra="forbid", strict=True)

    build_id: SafeIdentifier
    execution_id: SafeIdentifier
    status: ExecutionStatus
    tasks: list[TaskStateRecord] = Field(min_length=1, max_length=256)
    terminal_manifest_sha256: Annotated[
        str | None, Field(default=None, pattern=r"^[0-9a-f]{64}$")
    ] = None

    @field_validator("build_id", "execution_id")
    @classmethod
    def validate_ids(cls, value: str, info) -> str:
        return validate_safe_identifier(value, info.field_name)

    @model_validator(mode="after")
    def validate_state_invariants(self) -> Self:
        task_ids = [task.task_id.casefold() for task in self.tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("execution task IDs must be unique")
        statuses = [task.status for task in self.tasks]
        terminal_tasks = {"completed", "failed", "blocked", "interrupted"}
        if self.status == "completed" and any(status != "completed" for status in statuses):
            raise ValueError("completed execution requires every task completed")
        if self.status == "failed":
            if "failed" not in statuses or any(status not in terminal_tasks for status in statuses):
                raise ValueError("failed execution requires a failed task and all tasks terminal")
        if self.status == "interrupted":
            if "interrupted" not in statuses or any(
                status not in terminal_tasks for status in statuses
            ):
                raise ValueError("interrupted execution requires all tasks terminal")
        if self.status in {"completed", "failed"} and not self.terminal_manifest_sha256:
            raise ValueError("trusted terminal state requires a terminal manifest")
        return self


class GitInspectionEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    backend: Literal["dulwich"] = "dulwich"
    operation: Literal["discover", "status"]
    status: Literal[
        "repository_found", "clean", "dirty", "not_a_repository", "inspection_failed"
    ]
    detail: str = ""


class BaselineFileRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    path: Annotated[str, Field(strict=True, min_length=1, max_length=4096)]
    kind: Literal["file", "directory"]
    size: Annotated[int, Field(strict=True, ge=0)]
    sha256: Annotated[str | None, Field(default=None, pattern=r"^[0-9a-f]{64}$")]


class TargetBaseline(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    captured_at: str
    inventory: list[BaselineFileRecord]
    tree_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    git_status: list[str]
    resolved_git_root: str | None = None
    git_evidence: list[GitInspectionEvidence] = Field(default_factory=list)


class TargetAuthorityRecord(BaseModel):
    """Persisted decision binding execution to one target and prepared root."""

    model_config = ConfigDict(extra="forbid", strict=True)

    build_id: SafeIdentifier
    execution_id: SafeIdentifier
    idempotency_key: SafeIdentifier
    request_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    requested_target_root: str
    resolved_target_root: str
    resolved_git_root: str | None = None
    allowed_write_paths: list[str]
    allowed_resolved_paths: list[str]
    baseline_file_inventory: list[BaselineFileRecord]
    baseline_tree_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    baseline_git_status: list[str]
    baseline_git_evidence: list[GitInspectionEvidence] = Field(default_factory=list)
    prepared_plan_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    prepared_integrity_root: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    selected_provider: str
    authority_timestamp: str
    isolated_workspace_path: str


class WorkspaceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    created_at: str
    source_root: str
    workspace_root: str
    baseline_tree_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    workspace_tree_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    baseline_verified: bool
    excluded_paths: list[str]


class ActionBase(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    action_id: SafeIdentifier

    @field_validator("action_id")
    @classmethod
    def validate_action_id(cls, value: str) -> str:
        return validate_safe_identifier(value, "action_id")


class WriteFileAction(ActionBase):
    type: Literal["write_file"]
    path: Annotated[str, Field(strict=True, min_length=1, max_length=4096)]
    content: Annotated[str, Field(strict=True, max_length=1_000_000)]

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return validate_relative_path(value)


class CreateDirectoryAction(ActionBase):
    type: Literal["create_directory"]
    path: Annotated[str, Field(strict=True, min_length=1, max_length=4096)]

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return validate_relative_path(value)


class DeleteFileAction(ActionBase):
    type: Literal["delete_file"]
    path: Annotated[str, Field(strict=True, min_length=1, max_length=4096)]

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return validate_relative_path(value)


WorkerMutation = Annotated[
    Union[
        WriteFileAction,
        CreateDirectoryAction,
        DeleteFileAction,
    ],
    Field(discriminator="type"),
]


class WorkerActionPlan(BaseModel):
    """Closed JSON protocol returned inside the Audisor worker answer string."""

    model_config = ConfigDict(extra="forbid", strict=True)

    summary: Annotated[str, Field(strict=True, min_length=1, max_length=4000)]
    mutations: list[WorkerMutation] = Field(min_length=1, max_length=128)
    expected_changed_paths: list[
        Annotated[str, Field(strict=True, min_length=1, max_length=4096)]
    ] = Field(max_length=256)

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("summary must be non-empty")
        return value

    @field_validator("expected_changed_paths")
    @classmethod
    def validate_expected_paths(cls, values: list[str]) -> list[str]:
        seen: set[str] = set()
        for value in values:
            validate_relative_path(value)
            key = value.replace("\\", "/").casefold()
            if key in seen:
                raise ValueError("expected_changed_paths must be unique")
            seen.add(key)
        return values

    @model_validator(mode="after")
    def validate_mutation_ids(self) -> Self:
        seen: set[str] = set()
        for mutation in self.mutations:
            key = mutation.action_id.casefold()
            if key in seen:
                raise ValueError("mutation action IDs must be unique")
            seen.add(key)
        return self


class ChangeRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    path: str
    change: Literal["created", "modified", "deleted"]
    sha256_before: Annotated[str | None, Field(pattern=r"^[0-9a-f]{64}$")] = None
    sha256_after: Annotated[str | None, Field(pattern=r"^[0-9a-f]{64}$")] = None


class CommandEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    action_id: SafeIdentifier
    argv: list[str]
    resolved_working_directory: str
    start_timestamp: str
    end_timestamp: str
    exit_code: int | None
    stdout: str
    stderr: str
    stdout_truncated: bool
    stderr_truncated: bool
    timeout_status: bool
    output_limit_exceeded: bool = False
    sandbox_backend: str = "docker"
    changed_paths: list[ChangeRecord]


class ActionExecutionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    action_id: SafeIdentifier
    type: str
    status: Literal["running", "completed", "failed"]
    start_timestamp: str
    end_timestamp: str | None = None
    path: str | None = None
    byte_count: int | None = None
    content_sha256: Annotated[str | None, Field(pattern=r"^[0-9a-f]{64}$")] = None
    message: str | None = None


class SanitizedWorkerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    task_id: SafeIdentifier
    answer_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    answer_bytes: Annotated[int, Field(strict=True, ge=0)]
    answer_excerpt: str
    excerpt_truncated: bool


class TaskExecutionResult(BaseModel):
    """Durable result written before the task can become terminal."""

    model_config = ConfigDict(extra="forbid", strict=True)

    build_id: SafeIdentifier
    execution_id: SafeIdentifier
    task_id: SafeIdentifier
    status: Literal["completed", "failed", "blocked", "interrupted"]
    skill_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    plan_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    worker_input: dict[str, str]
    worker_dispatched: bool
    worker_output: SanitizedWorkerOutput | None
    requested_actions: WorkerActionPlan | None
    executed_actions: list[ActionExecutionRecord]
    changed_paths: list[ChangeRecord]
    validation_commands: list[CommandEvidence]
    exit_codes: list[int]
    prepared_validation_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    rendered_validation_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    executed_validation_sha256: Annotated[str | None, Field(pattern=r"^[0-9a-f]{64}$")]
    expected_outputs_verified: bool
    completion_timestamp: str
    error: str | None = None

    @model_validator(mode="after")
    def validate_result_evidence(self) -> Self:
        if self.status == "completed":
            if (
                self.validation_commands
                or self.exit_codes
                or self.executed_validation_sha256 is not None
                or not self.expected_outputs_verified
                or self.error is not None
            ):
                raise ValueError(
                    "completed mutation-only task requires static verification evidence"
                )
        elif not self.error:
            raise ValueError("non-completed task requires durable failure evidence")
        return self


class ExecutionEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    build_id: SafeIdentifier
    execution_id: SafeIdentifier
    task_id: SafeIdentifier
    result_path: str
    evidence_path: str
    result_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    completed_at: str
