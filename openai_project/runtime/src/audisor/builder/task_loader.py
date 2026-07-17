"""Prepared-build integrity manifest creation and execution-time verification."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from audisor.builder.coverage import validate_plan_coverage
from audisor.builder.dependencies import deterministic_topological_order
from audisor.builder.evidence import canonical_json_bytes, sha256_bytes
from audisor.builder.skill_renderer import RenderedSkill, render_skills
from audisor.schemas.build import BuildPlan, BuildRequest, SafeIdentifier

if TYPE_CHECKING:
    from audisor.builder.store import BuildStore

INTEGRITY_FILENAME = "integrity.json"
INTEGRITY_SCHEMA_VERSION = 1


class PreparedBuildError(RuntimeError):
    """Base prepared-build loading failure."""


class PreparedBuildNotFoundError(PreparedBuildError):
    """The requested prepared build does not exist."""


class PreparedBuildBlockedError(PreparedBuildError):
    """A blocked preparation cannot be executed."""


class PreparedBuildIntegrityError(PreparedBuildError):
    """Prepared artifacts do not match their consistency manifest."""


class IntegrityFile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    path: str
    byte_length: int = Field(strict=True, ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class IntegrityTask(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    task_id: SafeIdentifier
    task_record_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    skill_path: str
    skill_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class PreparedIntegrityManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: int = Field(strict=True, ge=1, le=1)
    algorithm: str
    build_id: SafeIdentifier
    files: list[IntegrityFile]
    tasks: list[IntegrityTask]
    root_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("algorithm")
    @classmethod
    def validate_algorithm(cls, value: str) -> str:
        if value != "sha256":
            raise ValueError("unsupported integrity algorithm")
        return value


@dataclass(frozen=True)
class LoadedPreparedBuild:
    build_path: Path
    instruction: BuildRequest
    plan: BuildPlan
    skills: dict[str, RenderedSkill]
    instruction_hash: str
    plan_hash: str
    task_hashes: dict[str, str]
    skill_hashes: dict[str, str]
    integrity_root: str


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> None:
    raise ValueError(f"non-finite JSON value: {value}")


def _strict_json_bytes(content: bytes) -> object:
    return json.loads(
        content.decode("utf-8"),
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=_reject_non_finite,
    )


def _manifest_body(
    build_id: str,
    files: list[dict[str, object]],
    tasks: list[dict[str, object]],
) -> dict[str, object]:
    return {
        "schema_version": INTEGRITY_SCHEMA_VERSION,
        "algorithm": "sha256",
        "build_id": build_id,
        "files": sorted(files, key=lambda item: str(item["path"])),
        "tasks": tasks,
    }


def create_integrity_manifest(
    instruction_text: str,
    plan_text: str,
    plan: BuildPlan,
    skills: list[RenderedSkill],
) -> dict[str, object]:
    """Create an unsigned consistency anchor over immutable prepared artifacts."""
    payloads: dict[str, bytes] = {
        "instruction.json": instruction_text.encode("utf-8"),
        "plan.json": plan_text.encode("utf-8"),
    }
    tasks: list[dict[str, object]] = []
    for task, skill in zip(plan.tasks, skills, strict=True):
        skill_path = f"skills/{skill.directory_name}/SKILL.md"
        skill_bytes = skill.content.encode("utf-8")
        payloads[skill_path] = skill_bytes
        tasks.append(
            {
                "task_id": task.task_id,
                "task_record_sha256": sha256_bytes(
                    canonical_json_bytes(task.model_dump(mode="json"))
                ),
                "skill_path": skill_path,
                "skill_sha256": sha256_bytes(skill_bytes),
            }
        )
    files = [
        {
            "path": path,
            "byte_length": len(content),
            "sha256": sha256_bytes(content),
        }
        for path, content in payloads.items()
    ]
    body = _manifest_body(plan.build_id, files, tasks)
    return {**body, "root_digest": sha256_bytes(canonical_json_bytes(body))}


def _is_reparse_or_symlink(path: Path) -> bool:
    try:
        stat_result = path.lstat()
    except OSError:
        return True
    attributes = getattr(stat_result, "st_file_attributes", 0)
    reparse_flag = getattr(__import__("stat"), "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(attributes & reparse_flag)


class PreparedBuildLoader:
    """Load only ready builds whose exact artifacts match the preparation anchor."""

    def __init__(self, store: "BuildStore") -> None:
        self.store = store

    def load(self, build_id: str) -> LoadedPreparedBuild:
        builds_root = self.store.builds_root.resolve()
        lexical_build_path = builds_root / build_id
        if not lexical_build_path.exists() and not lexical_build_path.is_symlink():
            raise PreparedBuildNotFoundError("Prepared build not found")
        if _is_reparse_or_symlink(lexical_build_path):
            raise PreparedBuildIntegrityError("Prepared build root is a reparse point")
        build_path = lexical_build_path.resolve()
        if build_path.parent != builds_root or not build_path.is_dir():
            raise PreparedBuildIntegrityError("Prepared build root is outside storage")
        executions_path = build_path / "executions"
        if (executions_path.exists() or executions_path.is_symlink()) and (
            _is_reparse_or_symlink(executions_path)
            or not executions_path.is_dir()
            or executions_path.resolve().parent != build_path
        ):
            raise PreparedBuildIntegrityError("Execution storage root is unsafe")
        manifest_path = build_path / INTEGRITY_FILENAME
        if not manifest_path.is_file() or _is_reparse_or_symlink(manifest_path):
            raise PreparedBuildIntegrityError("Prepared build integrity is unavailable")

        try:
            manifest = PreparedIntegrityManifest.model_validate(
                _strict_json_bytes(manifest_path.read_bytes())
            )
        except (OSError, UnicodeError, ValueError, ValidationError):
            raise PreparedBuildIntegrityError("Prepared build integrity is invalid") from None
        if manifest.build_id != build_id:
            raise PreparedBuildIntegrityError("Prepared build identity does not match")

        body = _manifest_body(
            manifest.build_id,
            [item.model_dump(mode="json") for item in manifest.files],
            [item.model_dump(mode="json") for item in manifest.tasks],
        )
        if sha256_bytes(canonical_json_bytes(body)) != manifest.root_digest:
            raise PreparedBuildIntegrityError("Prepared build integrity root does not match")

        expected_paths = [item.path for item in manifest.files]
        if len(expected_paths) != len(set(expected_paths)):
            raise PreparedBuildIntegrityError("Prepared build file inventory is duplicated")
        actual_paths: list[str] = []
        for root, directories, files in os.walk(build_path):
            root_path = Path(root)
            if root_path == build_path and "executions" in directories:
                directories.remove("executions")
            for directory in list(directories):
                directory_path = root_path / directory
                if _is_reparse_or_symlink(directory_path):
                    raise PreparedBuildIntegrityError("Prepared build contains a reparse point")
            for filename in files:
                file_path = root_path / filename
                relative = file_path.relative_to(build_path).as_posix()
                if relative == INTEGRITY_FILENAME:
                    continue
                if _is_reparse_or_symlink(file_path):
                    raise PreparedBuildIntegrityError("Prepared build contains a reparse point")
                actual_paths.append(relative)
        if sorted(actual_paths) != sorted(expected_paths):
            raise PreparedBuildIntegrityError("Prepared build file inventory does not match")

        payloads: dict[str, bytes] = {}
        for item in manifest.files:
            path = build_path / Path(*item.path.split("/"))
            try:
                content = path.read_bytes()
            except OSError:
                raise PreparedBuildIntegrityError("Prepared build artifact is missing") from None
            if len(content) != item.byte_length or sha256_bytes(content) != item.sha256:
                raise PreparedBuildIntegrityError("Prepared build artifact hash does not match")
            payloads[item.path] = content

        try:
            instruction = BuildRequest.model_validate(
                _strict_json_bytes(payloads["instruction.json"])
            )
            plan = BuildPlan.model_validate(_strict_json_bytes(payloads["plan.json"]))
        except (KeyError, UnicodeError, ValueError, ValidationError):
            raise PreparedBuildIntegrityError("Prepared build schema does not match") from None
        if instruction.build_id != build_id or plan.build_id != build_id:
            raise PreparedBuildIntegrityError("Prepared build identity does not match")
        if plan.status != "ready" or plan.gaps:
            raise PreparedBuildBlockedError("Blocked prepared builds cannot execute")

        ordered = deterministic_topological_order(plan.tasks)
        if [task.model_dump() for task in ordered] != [
            task.model_dump() for task in plan.tasks
        ]:
            raise PreparedBuildIntegrityError("Prepared task order is not canonical")
        validate_plan_coverage(plan.tasks)
        rendered = render_skills(build_id, plan.tasks)
        if len(rendered) != len(manifest.tasks):
            raise PreparedBuildIntegrityError("Prepared task inventory does not match")

        skills: dict[str, RenderedSkill] = {}
        task_hashes: dict[str, str] = {}
        skill_hashes: dict[str, str] = {}
        for task, skill, anchored in zip(plan.tasks, rendered, manifest.tasks, strict=True):
            expected_path = f"skills/{skill.directory_name}/SKILL.md"
            task_hash = sha256_bytes(canonical_json_bytes(task.model_dump(mode="json")))
            skill_hash = sha256_bytes(skill.content.encode("utf-8"))
            if (
                anchored.task_id != task.task_id
                or anchored.task_record_sha256 != task_hash
                or anchored.skill_path != expected_path
                or anchored.skill_sha256 != skill_hash
                or payloads.get(expected_path) != skill.content.encode("utf-8")
            ):
                raise PreparedBuildIntegrityError("Prepared task skill does not match")
            skills[task.task_id] = skill
            task_hashes[task.task_id] = task_hash
            skill_hashes[task.task_id] = skill_hash

        return LoadedPreparedBuild(
            build_path=build_path,
            instruction=instruction,
            plan=plan,
            skills=skills,
            instruction_hash=sha256_bytes(payloads["instruction.json"]),
            plan_hash=sha256_bytes(payloads["plan.json"]),
            task_hashes=task_hashes,
            skill_hashes=skill_hashes,
            integrity_root=manifest.root_digest,
        )
