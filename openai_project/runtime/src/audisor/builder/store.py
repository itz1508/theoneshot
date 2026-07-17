"""Atomic storage for prepared build instructions, plans, and task skills."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from audisor.builder.skill_renderer import (
    MAX_SKILL_DIRECTORY_LENGTH,
    SAFE_SKILL_DIRECTORY_RE,
    RenderedSkill,
)
from audisor.builder.task_loader import INTEGRITY_FILENAME, create_integrity_manifest
from audisor.schemas.build import BuildPlan, BuildRequest, validate_safe_identifier


class BuildStoreError(RuntimeError):
    """A prepared build could not be safely persisted."""


class BuildAlreadyExistsError(BuildStoreError):
    """A build ID already exists or is currently being published."""


@dataclass(frozen=True)
class BuildStore:
    """Publish complete build directories using a same-volume atomic rename."""

    data_dir: Path

    @classmethod
    def from_environment(cls) -> "BuildStore":
        configured = os.environ.get("AUDISOR_DATA_DIR", "").strip()
        if configured:
            selected = Path(configured).expanduser()
        elif os.name == "nt":
            local = os.environ.get("LOCALAPPDATA", "").strip()
            if not local:
                raise BuildStoreError("LOCALAPPDATA is required for secure data storage")
            selected = Path(local) / "Audisor" / "data"
        else:
            base = os.environ.get("XDG_DATA_HOME", "").strip()
            selected = (
                Path(base).expanduser()
                if base
                else Path.home() / ".local" / "share"
            ) / "audisor"
        product = Path(__file__).resolve().parents[4]
        snapshot = product.parent / "snapshot"
        development = product.parent.parent
        protected = (
            product,
            snapshot,
            product.parent / "audisor",
            development / "amd",
            development / "hackaton-uipath-jun29-workbench",
            development / "Edge",
            product.parent / "audisor" / ".agents" / "skills",
        )
        resolved = selected.resolve(strict=False)
        for root in protected:
            root = root.resolve(strict=False)
            try:
                overlaps = os.path.commonpath([str(resolved), str(root)]) in {
                    str(resolved), str(root)
                }
            except ValueError:
                overlaps = False
            if overlaps:
                raise BuildStoreError("AUDISOR_DATA_DIR overlaps a protected path")
        return cls(resolved)

    @property
    def legacy_data_dir(self) -> Path:
        """Historical product-local data is discoverable but never selected by default."""
        return Path(__file__).resolve().parents[4] / ".audisor"

    @property
    def builds_root(self) -> Path:
        return self.data_dir.resolve() / "builds"

    def build_path(self, build_id: str) -> Path:
        validate_safe_identifier(build_id, "build_id")
        root = self.builds_root.resolve(strict=False)
        candidate = root / build_id
        if os.path.normcase(os.path.normpath(str(candidate.parent))) != os.path.normcase(
            os.path.normpath(str(root))
        ):
            raise BuildStoreError("Build storage failed")
        if candidate.is_symlink():
            raise BuildStoreError("Build storage failed")
        return candidate

    def assert_available(self, build_id: str) -> None:
        """Reject an existing final build without creating storage directories."""
        if self.build_path(build_id).exists():
            raise BuildAlreadyExistsError("Build already exists")

    @staticmethod
    def _json_text(payload: object) -> str:
        return json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n"

    def _write_text(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")

    def _publish_directory(self, source: Path, destination: Path) -> None:
        """Rename without replacement; caller holds the build-specific lock."""
        source.rename(destination)

    def _validate_publication(
        self,
        instruction: BuildRequest,
        plan: BuildPlan,
        skills: list[RenderedSkill],
    ) -> None:
        validate_safe_identifier(instruction.build_id, "build_id")
        validate_safe_identifier(plan.build_id, "build_id")
        if instruction.build_id != plan.build_id:
            raise BuildStoreError("Build storage failed")

        expected_task_ids = [task.task_id for task in plan.tasks]
        actual_task_ids = [skill.task_id for skill in skills]
        if expected_task_ids != actual_task_ids:
            raise BuildStoreError("Build storage failed")
        if plan.status == "blocked" and skills:
            raise BuildStoreError("Build storage failed")

        seen_directories: set[str] = set()
        for skill in skills:
            skill.as_worker_task()
            if (
                len(skill.directory_name) > MAX_SKILL_DIRECTORY_LENGTH
                or not SAFE_SKILL_DIRECTORY_RE.fullmatch(skill.directory_name)
            ):
                raise BuildStoreError("Build storage failed")
            key = skill.directory_name.casefold()
            if key in seen_directories:
                raise BuildStoreError("Build storage failed")
            seen_directories.add(key)

    def publish(
        self,
        instruction: BuildRequest,
        plan: BuildPlan,
        skills: list[RenderedSkill],
    ) -> Path:
        """Validate, stage, and atomically publish one complete build."""
        try:
            self._validate_publication(instruction, plan, skills)
        except BuildStoreError:
            raise
        except (TypeError, ValueError):
            raise BuildStoreError("Build storage failed") from None
        final_path = self.build_path(instruction.build_id)
        if final_path.exists():
            raise BuildAlreadyExistsError("Build already exists")

        root = self.builds_root
        root.mkdir(parents=True, exist_ok=True)
        lock_path = root / f".{instruction.build_id}.lock"
        lock_acquired = False
        temporary_path: Path | None = None

        try:
            try:
                descriptor = os.open(
                    lock_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                )
            except FileExistsError:
                raise BuildAlreadyExistsError(
                    "Build already exists or is being prepared"
                ) from None
            else:
                os.close(descriptor)
                lock_acquired = True

            if final_path.exists():
                raise BuildAlreadyExistsError("Build already exists")

            temporary_path = Path(
                tempfile.mkdtemp(
                    prefix=f".{instruction.build_id}.",
                    suffix=".tmp",
                    dir=root,
                )
            ).resolve()
            if temporary_path.parent != root:
                raise BuildStoreError("Build storage failed")

            skills_path = temporary_path / "skills"
            skills_path.mkdir()
            instruction_text = self._json_text(instruction.model_dump(mode="json"))
            plan_text = self._json_text(plan.model_dump(mode="json"))
            self._write_text(
                temporary_path / "instruction.json",
                instruction_text,
            )
            self._write_text(
                temporary_path / "plan.json",
                plan_text,
            )
            for skill in skills:
                self._write_text(
                    skills_path / skill.directory_name / "SKILL.md",
                    skill.content,
                )
            integrity = create_integrity_manifest(
                instruction_text,
                plan_text,
                plan,
                skills,
            )
            self._write_text(
                temporary_path / INTEGRITY_FILENAME,
                self._json_text(integrity),
            )

            if final_path.exists():
                raise BuildAlreadyExistsError("Build already exists")
            try:
                self._publish_directory(temporary_path, final_path)
            except FileExistsError:
                raise BuildAlreadyExistsError("Build already exists") from None
            temporary_path = None
            return final_path
        except BuildAlreadyExistsError:
            raise
        except BuildStoreError:
            raise
        except (OSError, RuntimeError, ValueError):
            raise BuildStoreError("Build storage failed") from None
        finally:
            if temporary_path is not None and temporary_path.exists():
                if (
                    temporary_path.parent == root
                    and temporary_path.name.startswith(f".{instruction.build_id}.")
                    and temporary_path.name.endswith(".tmp")
                ):
                    shutil.rmtree(temporary_path)
            if lock_acquired:
                try:
                    lock_path.unlink(missing_ok=True)
                except OSError:
                    pass
