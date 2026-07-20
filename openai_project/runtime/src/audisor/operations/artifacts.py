"""Artifact persistence for Audisor operations.

Provides artifact manifesting and reference tracking for operation outputs.
Replaces BuildStore artifact handling with a generic, host-agnostic system.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping

from audisor.schemas.errors import AudisorRuntimeError


@dataclass(frozen=True)
class ArtifactReference:
    """Reference to a persisted artifact."""

    artifact_id: str
    artifact_type: Literal["file", "log", "report", "contract", "lock", "analysis"]
    path: str | None = None
    content_hash: str | None = None
    size_bytes: int | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))

    def to_mapping(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "artifact_type": self.artifact_type,
            "path": self.path,
            "content_hash": self.content_hash,
            "size_bytes": self.size_bytes,
            "created_at": self.created_at,
        }


@dataclass
class ArtifactManifest:
    """Manifest of artifacts produced by an operation."""

    operation_id: str
    artifacts: list[ArtifactReference] = field(default_factory=list)

    def add_artifact(
        self,
        artifact_id: str,
        artifact_type: Literal["file", "log", "report", "contract", "lock", "analysis"],
        path: str | None = None,
        content: bytes | str | None = None,
    ) -> ArtifactReference:
        """Add an artifact to the manifest, computing hash if content provided."""
        content_hash = None
        size_bytes = None
        if content is not None:
            if isinstance(content, str):
                content = content.encode("utf-8")
            content_hash = hashlib.sha256(content).hexdigest()
            size_bytes = len(content)
        ref = ArtifactReference(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            path=path,
            content_hash=content_hash,
            size_bytes=size_bytes,
        )
        self.artifacts.append(ref)
        return ref

    def to_mapping(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "artifacts": [a.to_mapping() for a in self.artifacts],
        }

    def get_by_type(self, artifact_type: str) -> list[ArtifactReference]:
        """Return all artifacts of a given type."""
        return [a for a in self.artifacts if a.artifact_type == artifact_type]


class ArtifactStore:
    """Filesystem-based artifact store for operation outputs.

    Production implementations may replace this with object storage.
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def _operation_dir(self, operation_id: str) -> Path:
        # Sanitize operation_id for filesystem use
        safe_id = "".join(c for c in operation_id if c.isalnum() or c in "-_.")
        return self._root / safe_id

    def persist(
        self,
        operation_id: str,
        artifact_id: str,
        content: bytes | str,
        *,
        artifact_type: Literal["file", "log", "report", "contract", "lock", "analysis"] = "file",
        extension: str = ".json",
    ) -> ArtifactReference:
        """Persist artifact content and return a reference."""
        op_dir = self._operation_dir(operation_id)
        op_dir.mkdir(parents=True, exist_ok=True)

        if isinstance(content, str):
            content = content.encode("utf-8")

        safe_artifact_id = "".join(c for c in artifact_id if c.isalnum() or c in "-_.")
        path = op_dir / f"{safe_artifact_id}{extension}"

        # Atomic write
        temp_path = path.with_suffix(".tmp")
        temp_path.write_bytes(content)
        temp_path.replace(path)

        ref = ArtifactReference(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            path=str(path),
            content_hash=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
        )
        return ref

    def load(self, ref: ArtifactReference) -> bytes:
        """Load artifact content by reference."""
        if ref.path is None:
            raise AudisorRuntimeError(
                category="storage",
                stage="execution",
                code="artifact_path_missing",
                message="Artifact has no persisted path",
            )
        path = Path(ref.path)
        if not path.exists():
            raise AudisorRuntimeError(
                category="storage",
                stage="execution",
                code="artifact_not_found",
                message=f"Artifact not found: {path}",
            )
        return path.read_bytes()

    def load_json(self, ref: ArtifactReference) -> Any:
        """Load artifact content as JSON."""
        import json
        return json.loads(self.load(ref).decode("utf-8"))

    def list_operation_artifacts(self, operation_id: str) -> list[ArtifactReference]:
        """List all artifacts for an operation."""
        op_dir = self._operation_dir(operation_id)
        if not op_dir.exists():
            return []
        refs: list[ArtifactReference] = []
        for path in op_dir.iterdir():
            if path.suffix == ".tmp":
                continue
            content = path.read_bytes()
            refs.append(ArtifactReference(
                artifact_id=path.stem,
                artifact_type="file",  # Default; caller should know type
                path=str(path),
                content_hash=hashlib.sha256(content).hexdigest(),
                size_bytes=len(content),
            ))
        return refs