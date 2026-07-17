"""Atomic terminal evidence manifests and fail-closed reconciliation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Mapping, Sequence

from audisor.builder.evidence import atomic_write_bytes
from audisor.builder.safe_json import json_safe_bytes

TERMINAL_MANIFEST_NAME = "terminal-manifest.json"
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,62}[A-Za-z0-9])?$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_WINDOWS_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}


class TerminalManifestError(RuntimeError):
    """Terminal evidence could not be written or verified safely."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_reparse_or_symlink(path: Path) -> bool:
    try:
        status = path.lstat()
    except OSError:
        return False
    attributes = getattr(status, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(attributes & reparse_flag)


def _validate_task_id(value: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID_RE.fullmatch(value):
        raise TerminalManifestError("Expected task ID is unsafe")
    return value


def _relative_text(value: str | Path) -> str:
    supplied = str(value).replace("\\", "/")
    windows = PureWindowsPath(str(value))
    posix = PurePosixPath(supplied)
    if (
        not supplied
        or supplied != supplied.strip()
        or "\x00" in supplied
        or supplied.startswith(("//", "/", "\\"))
        or windows.drive
        or windows.root
        or posix.is_absolute()
        or ":" in supplied
        or any(part in {"", ".", ".."} for part in posix.parts)
    ):
        raise TerminalManifestError("Manifest artifact path is unsafe")
    for part in posix.parts:
        if (
            part.endswith((".", " "))
            or part.split(".", 1)[0].casefold() in _WINDOWS_RESERVED
        ):
            raise TerminalManifestError("Manifest artifact path is unsafe")
    normalized = posix.as_posix()
    if normalized == TERMINAL_MANIFEST_NAME or normalized == "state.json":
        raise TerminalManifestError("Manifest cannot hash itself or terminal state")
    return normalized


def _execution_root(path: Path) -> Path:
    if not path.exists() or not path.is_dir() or _is_reparse_or_symlink(path):
        raise TerminalManifestError("Execution root is unsafe")
    return path.resolve(strict=True)


def _resolved_artifact(root: Path, relative: str) -> Path:
    candidate = root.joinpath(*PurePosixPath(relative).parts)
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        if _is_reparse_or_symlink(current):
            raise TerminalManifestError("Manifest artifact traverses a symlink or reparse point")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        raise TerminalManifestError("Required terminal artifact is missing") from None
    try:
        if os.path.commonpath([str(resolved), str(root)]) != str(root):
            raise TerminalManifestError("Manifest artifact escapes the execution root")
    except ValueError:
        raise TerminalManifestError("Manifest artifact escapes the execution root") from None
    return resolved


def _expand_artifact(root: Path, supplied: str | Path) -> tuple[str, ...]:
    relative = _relative_text(supplied)
    resolved = _resolved_artifact(root, relative)
    if resolved.is_file():
        return (relative,)
    if not resolved.is_dir():
        raise TerminalManifestError("Required terminal artifact is not a file or directory")
    files: list[str] = []
    for candidate in sorted(resolved.rglob("*"), key=lambda item: item.as_posix()):
        if _is_reparse_or_symlink(candidate):
            raise TerminalManifestError("Manifest artifact directory contains a reparse point")
        if candidate.is_file():
            files.append(candidate.relative_to(root).as_posix())
    if not files:
        raise TerminalManifestError("Required terminal evidence directory is empty")
    return tuple(files)


@dataclass(frozen=True)
class TaskArtifactPaths:
    result_path: str
    evidence_paths: tuple[str, ...]


@dataclass(frozen=True)
class TerminalArtifactRecord:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class TerminalTaskRecord:
    task_id: str
    result_path: str
    evidence_paths: tuple[str, ...]


@dataclass(frozen=True)
class TerminalManifestRecord:
    schema_version: int
    build_id: str
    execution_id: str
    created_at: str
    expected_task_ids: tuple[str, ...]
    authority_artifacts: tuple[str, ...]
    tasks: tuple[TerminalTaskRecord, ...]
    artifacts: tuple[TerminalArtifactRecord, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "build_id": self.build_id,
            "execution_id": self.execution_id,
            "created_at": self.created_at,
            "expected_task_ids": list(self.expected_task_ids),
            "authority_artifacts": list(self.authority_artifacts),
            "tasks": [
                {
                    **asdict(task),
                    "evidence_paths": list(task.evidence_paths),
                }
                for task in self.tasks
            ],
            "artifacts": [asdict(artifact) for artifact in self.artifacts],
        }


@dataclass(frozen=True)
class TerminalManifestWriteResult:
    path: Path
    sha256: str
    record: TerminalManifestRecord


@dataclass(frozen=True)
class TerminalManifestVerification:
    valid: bool
    manifest_sha256: str | None
    errors: tuple[str, ...]
    record: TerminalManifestRecord | None


def _manifest_from_payload(payload: object) -> TerminalManifestRecord:
    required = {
        "schema_version",
        "build_id",
        "execution_id",
        "created_at",
        "expected_task_ids",
        "authority_artifacts",
        "tasks",
        "artifacts",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise TerminalManifestError("Terminal manifest fields are invalid")
    if payload["schema_version"] != 1:
        raise TerminalManifestError("Terminal manifest version is invalid")
    for name in ("build_id", "execution_id", "created_at"):
        if not isinstance(payload[name], str) or not payload[name]:
            raise TerminalManifestError("Terminal manifest identity is invalid")
    task_ids_payload = payload["expected_task_ids"]
    authority_payload = payload["authority_artifacts"]
    tasks_payload = payload["tasks"]
    artifacts_payload = payload["artifacts"]
    if (
        not isinstance(task_ids_payload, list)
        or not isinstance(authority_payload, list)
        or not isinstance(tasks_payload, list)
    ):
        raise TerminalManifestError("Terminal manifest task data is invalid")
    if not isinstance(artifacts_payload, list):
        raise TerminalManifestError("Terminal manifest artifact data is invalid")
    expected_ids = tuple(_validate_task_id(task_id) for task_id in task_ids_payload)
    if len(set(expected_ids)) != len(expected_ids) or not expected_ids:
        raise TerminalManifestError("Terminal manifest expected task IDs are invalid")
    authority_artifacts = tuple(
        _relative_text(path) for path in authority_payload
    )
    if len(set(authority_artifacts)) != len(authority_artifacts):
        raise TerminalManifestError("Terminal authority artifacts are duplicated")

    tasks: list[TerminalTaskRecord] = []
    for item in tasks_payload:
        if not isinstance(item, dict) or set(item) != {
            "task_id",
            "result_path",
            "evidence_paths",
        }:
            raise TerminalManifestError("Terminal manifest task record is invalid")
        if not isinstance(item["evidence_paths"], list) or not item["evidence_paths"]:
            raise TerminalManifestError("Terminal task evidence list is invalid")
        task = TerminalTaskRecord(
            task_id=_validate_task_id(item["task_id"]),
            result_path=_relative_text(item["result_path"]),
            evidence_paths=tuple(_relative_text(path) for path in item["evidence_paths"]),
        )
        if task.result_path != f"results/{task.task_id}.json":
            raise TerminalManifestError("Terminal task result path is invalid")
        if any(
            not path.startswith(f"evidence/{task.task_id}/")
            for path in task.evidence_paths
        ):
            raise TerminalManifestError("Terminal task evidence path is invalid")
        tasks.append(task)
    if tuple(task.task_id for task in tasks) != expected_ids:
        raise TerminalManifestError("Terminal task records do not match expected tasks")

    artifacts: list[TerminalArtifactRecord] = []
    seen_paths: set[str] = set()
    for item in artifacts_payload:
        if not isinstance(item, dict) or set(item) != {"path", "size", "sha256"}:
            raise TerminalManifestError("Terminal manifest artifact record is invalid")
        relative = _relative_text(item["path"])
        if relative in seen_paths:
            raise TerminalManifestError("Terminal manifest artifact paths are duplicated")
        seen_paths.add(relative)
        size = item["size"]
        digest = item["sha256"]
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise TerminalManifestError("Terminal artifact size is invalid")
        if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
            raise TerminalManifestError("Terminal artifact hash is invalid")
        artifacts.append(TerminalArtifactRecord(path=relative, size=size, sha256=digest))
    if not artifacts:
        raise TerminalManifestError("Terminal manifest has no artifacts")
    return TerminalManifestRecord(
        schema_version=1,
        build_id=payload["build_id"],
        execution_id=payload["execution_id"],
        created_at=payload["created_at"],
        expected_task_ids=expected_ids,
        authority_artifacts=authority_artifacts,
        tasks=tuple(tasks),
        artifacts=tuple(artifacts),
    )


def write_terminal_manifest(
    execution_root: Path,
    *,
    build_id: str,
    execution_id: str,
    expected_task_ids: Sequence[str],
    task_artifacts: Mapping[str, TaskArtifactPaths],
    required_artifacts: Sequence[str | Path],
    authority_artifacts: Sequence[str | Path] = (),
) -> TerminalManifestWriteResult:
    """Hash complete task/evidence artifacts, then atomically publish the manifest."""
    root = _execution_root(execution_root)
    expected = tuple(_validate_task_id(task_id) for task_id in expected_task_ids)
    if not expected or len(set(expected)) != len(expected):
        raise TerminalManifestError("Expected task IDs must be non-empty and unique")
    if set(task_artifacts) != set(expected):
        raise TerminalManifestError("Every expected task must have result and evidence paths")

    artifact_paths: set[str] = set()
    task_records: list[TerminalTaskRecord] = []
    for task_id in expected:
        paths = task_artifacts[task_id]
        result_path = _relative_text(paths.result_path)
        expected_result_path = f"results/{task_id}.json"
        if result_path != expected_result_path:
            raise TerminalManifestError("Task result path does not match its task ID")
        expanded_result = _expand_artifact(root, result_path)
        if expanded_result != (result_path,):
            raise TerminalManifestError("Task result must be one file")
        expanded_evidence: set[str] = set()
        if not paths.evidence_paths:
            raise TerminalManifestError("Every expected task must have evidence")
        evidence_prefix = f"evidence/{task_id}/"
        for path in paths.evidence_paths:
            for expanded in _expand_artifact(root, path):
                if not expanded.startswith(evidence_prefix):
                    raise TerminalManifestError("Task evidence path does not match its task ID")
                expanded_evidence.add(expanded)
        if not expanded_evidence:
            raise TerminalManifestError("Every expected task must have evidence")
        ordered_evidence = tuple(sorted(expanded_evidence))
        artifact_paths.add(result_path)
        artifact_paths.update(ordered_evidence)
        task_records.append(
            TerminalTaskRecord(
                task_id=task_id,
                result_path=result_path,
                evidence_paths=ordered_evidence,
            )
        )

    for supplied in required_artifacts:
        artifact_paths.update(_expand_artifact(root, supplied))
    expanded_authority: list[str] = []
    for supplied in authority_artifacts:
        expanded = _expand_artifact(root, supplied)
        expanded_authority.extend(expanded)
        artifact_paths.update(expanded)
    if len(set(expanded_authority)) != len(expanded_authority):
        raise TerminalManifestError("Terminal authority artifacts are duplicated")
    if not artifact_paths:
        raise TerminalManifestError("Terminal manifest has no artifacts")

    records: list[TerminalArtifactRecord] = []
    for relative in sorted(artifact_paths):
        path = _resolved_artifact(root, relative)
        if not path.is_file():
            raise TerminalManifestError("Terminal manifest artifacts must be files")
        content = path.read_bytes()
        records.append(
            TerminalArtifactRecord(
                path=relative,
                size=len(content),
                sha256=hashlib.sha256(content).hexdigest(),
            )
        )
    manifest = TerminalManifestRecord(
        schema_version=1,
        build_id=build_id,
        execution_id=execution_id,
        created_at=_utc_now(),
        expected_task_ids=expected,
        authority_artifacts=tuple(expanded_authority),
        tasks=tuple(task_records),
        artifacts=tuple(records),
    )
    content = json_safe_bytes(manifest.to_dict(), indent=2)
    path = root / TERMINAL_MANIFEST_NAME
    atomic_write_bytes(path, content)
    return TerminalManifestWriteResult(
        path=path,
        sha256=hashlib.sha256(content).hexdigest(),
        record=manifest,
    )


def verify_terminal_manifest(
    execution_root: Path,
    *,
    expected_sha256: str,
    expected_task_ids: Sequence[str] | None = None,
) -> TerminalManifestVerification:
    """Verify manifest identity, task coverage, and every listed artifact hash."""
    errors: list[str] = []
    manifest_digest: str | None = None
    record: TerminalManifestRecord | None = None
    try:
        if not _SHA256_RE.fullmatch(expected_sha256):
            raise TerminalManifestError("Expected terminal manifest hash is invalid")
        root = _execution_root(execution_root)
        path = root / TERMINAL_MANIFEST_NAME
        if _is_reparse_or_symlink(path) or not path.is_file():
            raise TerminalManifestError("Terminal manifest is missing or unsafe")
        content = path.read_bytes()
        manifest_digest = hashlib.sha256(content).hexdigest()
        if manifest_digest != expected_sha256:
            errors.append("terminal manifest hash mismatch")
        try:
            payload = json.loads(content.decode("utf-8", errors="strict"))
        except (UnicodeError, json.JSONDecodeError):
            raise TerminalManifestError("Terminal manifest JSON is invalid") from None
        record = _manifest_from_payload(payload)
        if expected_task_ids is not None:
            expected = tuple(_validate_task_id(task_id) for task_id in expected_task_ids)
            if record.expected_task_ids != expected:
                errors.append("terminal manifest expected task mismatch")

        artifact_table = {artifact.path: artifact for artifact in record.artifacts}
        if any(path not in artifact_table for path in record.authority_artifacts):
            errors.append("terminal authority evidence is not fully hashed")
        for task in record.tasks:
            if task.result_path not in artifact_table:
                errors.append(f"task result is not hashed: {task.task_id}")
            if not task.evidence_paths or any(
                path not in artifact_table for path in task.evidence_paths
            ):
                errors.append(f"task evidence is not fully hashed: {task.task_id}")
        for artifact in record.artifacts:
            try:
                path = _resolved_artifact(root, artifact.path)
                if not path.is_file():
                    raise TerminalManifestError("artifact is not a file")
                content = path.read_bytes()
            except (OSError, TerminalManifestError):
                errors.append(f"terminal artifact is missing or unsafe: {artifact.path}")
                continue
            if len(content) != artifact.size:
                errors.append(f"terminal artifact size mismatch: {artifact.path}")
            if hashlib.sha256(content).hexdigest() != artifact.sha256:
                errors.append(f"terminal artifact hash mismatch: {artifact.path}")
    except (OSError, TerminalManifestError) as error:
        errors.append(str(error))
    return TerminalManifestVerification(
        valid=not errors,
        manifest_sha256=manifest_digest,
        errors=tuple(errors),
        record=record,
    )


def require_valid_terminal_manifest(
    execution_root: Path,
    *,
    expected_sha256: str,
    expected_task_ids: Sequence[str] | None = None,
) -> TerminalManifestRecord:
    """Raise instead of returning an untrusted terminal state."""
    verification = verify_terminal_manifest(
        execution_root,
        expected_sha256=expected_sha256,
        expected_task_ids=expected_task_ids,
    )
    if not verification.valid or verification.record is None:
        detail = "; ".join(verification.errors) or "unknown verification failure"
        raise TerminalManifestError(f"Terminal manifest reconciliation failed: {detail}")
    return verification.record
