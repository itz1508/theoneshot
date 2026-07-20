"""Audisor canonical operation runtime.

Provides host-agnostic operation execution, persistence, and result handling.
All host adapters (Codex, MCP, CLI, Responses-compatible) converge here.
"""

from audisor.operations.executor import AudisorOperationExecutor
from audisor.operations.store import AudisorOperationStore
from audisor.operations.context import AudisorOperationContext
from audisor.operations.result import AudisorOperationResult
from audisor.operations.artifacts import ArtifactManifest, ArtifactReference

__all__ = [
    "AudisorOperationExecutor",
    "AudisorOperationStore",
    "AudisorOperationContext",
    "AudisorOperationResult",
    "ArtifactManifest",
    "ArtifactReference",
]