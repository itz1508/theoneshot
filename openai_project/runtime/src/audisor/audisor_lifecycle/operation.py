"""Host-owned immutable Audisor operation policy and context."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal, Mapping

from audisor.config import OLLAMA_BASE_URL, OLLAMA_MODEL_ID, is_aflow_enabled
from audisor.schemas.authority import AuthorityContext
from .analysis_package import FrozenAnalysisPackage


@dataclass(frozen=True)
class FrozenAudisorPolicy:
    enabled: bool
    provider: str
    model_id: str
    base_url: str
    timeout_seconds: float = 300.0


@dataclass(frozen=True)
class AudisorOperationContext:
    operation_id: str
    operation_type: Literal["build", "fix"]
    accepted_task: Mapping[str, Any]
    accepted_plan: Mapping[str, Any]
    repository_context: Mapping[str, Any]
    workspace_identity: Mapping[str, Any]
    authority_context: Mapping[str, Any]
    context_sha256: str
    analysis_package: FrozenAnalysisPackage | None = None


def read_frozen_audisor_policy() -> FrozenAudisorPolicy:
    """Read configuration once at the host boundary."""
    return FrozenAudisorPolicy(
        enabled=is_aflow_enabled(),
        provider="local-openai-compatible",
        model_id=OLLAMA_MODEL_ID,
        base_url=OLLAMA_BASE_URL,
    )


def make_operation_context(
    *,
    operation_id: str,
    operation_type: Literal["build", "fix"],
    accepted_task: Mapping[str, Any],
    accepted_plan: Mapping[str, Any],
    repository_context: Mapping[str, Any],
    workspace_identity: Mapping[str, Any],
    authority_context: Mapping[str, Any] | AuthorityContext,
    analysis_package: FrozenAnalysisPackage | None = None,
) -> AudisorOperationContext:
    # Convert canonical AuthorityContext to mapping for internal storage
    if isinstance(authority_context, AuthorityContext):
        authority_mapping = authority_context.to_mapping()
    else:
        authority_mapping = dict(authority_context)
    body = {
        "operation_id": operation_id,
        "operation_type": operation_type,
        "accepted_task": dict(accepted_task),
        "accepted_plan": dict(accepted_plan),
        "repository_context": dict(repository_context),
        "workspace_identity": dict(workspace_identity),
        "authority_context": authority_mapping,
    }
    digest_body = {
        **body,
        "analysis_package_hash": analysis_package.package_hash if analysis_package else None,
    }
    digest = hashlib.sha256(json.dumps(digest_body, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()
    return AudisorOperationContext(**body, context_sha256=digest, analysis_package=analysis_package)