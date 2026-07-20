"""Host-owned assembly of the immutable frozen Audisor analysis package.

The model receives only the frozen ``analysis-request`` document.  Host
metadata is retained beside it for binding and audit; it is never merged
into the model document, whose schema forbids additional properties.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


class AnalysisPackageError(ValueError):
    """The accepted operation did not contain a valid frozen package."""


SCHEMA_VERSION = "1.0.0"
ANALYSIS_REQUEST_SCHEMA_ID = (
    "https://theoneshot.dev/schemas/aflow/v1/analysis-request.schema.json"
)


def _schema_root() -> Path:
    env_val = os.environ.get("AUDISOR_SCHEMA_ROOT") or os.environ.get("AFLOW_SCHEMA_ROOT")
    configured = Path(env_val) if env_val else None
    candidates = [
        configured,
        Path(__file__).resolve().parents[4] / "aflow" / "schemas" / "v1",
        Path.cwd() / "openai_project" / "aflow" / "schemas" / "v1",
    ]
    for candidate in candidates:
        if candidate is not None and (candidate / "analysis-request.schema.json").is_file():
            return candidate
    raise AnalysisPackageError("frozen Audisor schema directory is unavailable")


def _registry() -> tuple[dict[str, dict[str, Any]], Registry]:
    root = _schema_root()
    documents = {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in root.glob("*.schema.json")
    }
    registry = Registry()
    for name, document in documents.items():
        resource = Resource.from_contents(document)
        registry = registry.with_resource(document["$id"], resource)
        registry = registry.with_resource(name, resource)
    return documents, registry


def _canonical_bytes(value: Any) -> bytes:
    def normalize(item: Any) -> Any:
        if isinstance(item, Mapping):
            return {str(key): normalize(val) for key, val in item.items()}
        if isinstance(item, (list, tuple)):
            return [normalize(val) for val in item]
        return item

    return json.dumps(normalize(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_deep_freeze(item) for item in value)
    return value


def canonical_package_bytes(value: Mapping[str, Any]) -> bytes:
    """Return the exact bytes used for the host package seal."""
    return _canonical_bytes(value)


def package_sha256(value: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonical_package_bytes(value)).hexdigest()


@dataclass(frozen=True)
class FrozenAnalysisPackage:
    """Immutable host binding around the exact frozen analysis request."""

    operation_id: str
    operation_type: str
    accepted_task: Mapping[str, Any]
    accepted_plan: Mapping[str, Any]
    authority_context: Mapping[str, Any]
    repository_context: Mapping[str, Any]
    analysis_request: Mapping[str, Any]
    workspace_identity: Mapping[str, Any]
    provider_policy: Mapping[str, Any]
    package_hash: str

    @property
    def model_input(self) -> Mapping[str, Any]:
        # Return a detached JSON-compatible snapshot.  The sealed package
        # remains immutable while the transport layer receives ordinary JSON
        # containers.
        return json.loads(_canonical_bytes(self.analysis_request).decode("utf-8"))

    @property
    def canonical_payload(self) -> Mapping[str, Any]:
        return {
            "operation_id": self.operation_id,
            "operation_type": self.operation_type,
            "accepted_task": json.loads(_canonical_bytes(self.accepted_task)),
            "accepted_plan": json.loads(_canonical_bytes(self.accepted_plan)),
            "authority_context": json.loads(_canonical_bytes(self.authority_context)),
            "repository_context": json.loads(_canonical_bytes(self.repository_context)),
            "workspace_identity": json.loads(_canonical_bytes(self.workspace_identity)),
            "provider_policy": json.loads(_canonical_bytes(self.provider_policy)),
            "analysis_request": self.model_input,
        }


def validate_analysis_request(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise AnalysisPackageError("analysis request must be an object")
    documents, registry = _registry()
    schema = documents["analysis-request.schema.json"]
    errors = sorted(
        Draft202012Validator(schema, registry=registry, format_checker=FormatChecker()).iter_errors(value),
        key=lambda error: (list(error.absolute_path), error.message),
    )
    if errors:
        detail = "; ".join(f"/{'/'.join(map(str, error.absolute_path))}: {error.message}" for error in errors[:8])
        raise AnalysisPackageError(f"analysis-request schema validation failed: {detail}")
    result = json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True))
    if result.get("schema_version") != SCHEMA_VERSION:
        raise AnalysisPackageError("analysis-request schema version is not frozen v1.0.0")
    return result


def assemble_analysis_package(
    *,
    operation_id: str,
    operation_type: str,
    accepted_task: Mapping[str, Any],
    accepted_plan: Mapping[str, Any],
    authority_context: Mapping[str, Any],
    analysis_request: Mapping[str, Any],
    repository_context: Mapping[str, Any],
    workspace_identity: Mapping[str, Any],
    provider_policy: Mapping[str, Any],
) -> FrozenAnalysisPackage:
    if not operation_id or operation_type not in {"build", "fix"}:
        raise AnalysisPackageError("invalid Audisor operation identity")
    request = validate_analysis_request(analysis_request)
    analysis_id = request.get("analysis_id")
    if analysis_id != operation_id:
        raise AnalysisPackageError("analysis_id must equal the host operation_id")
    if not isinstance(workspace_identity, Mapping) or not workspace_identity:
        raise AnalysisPackageError("workspace identity is required")
    if not isinstance(provider_policy, Mapping) or not provider_policy:
        raise AnalysisPackageError("provider/model policy is required")
    frozen_request = _deep_freeze(request)
    package = FrozenAnalysisPackage(
        operation_id=operation_id,
        operation_type=operation_type,
        accepted_task=_deep_freeze(dict(accepted_task)),
        accepted_plan=_deep_freeze(dict(accepted_plan)),
        authority_context=_deep_freeze(dict(authority_context)),
        repository_context=_deep_freeze(dict(repository_context)),
        analysis_request=frozen_request,
        workspace_identity=_deep_freeze(dict(workspace_identity)),
        provider_policy=_deep_freeze(dict(provider_policy)),
        package_hash="",
    )
    return FrozenAnalysisPackage(
        operation_id=package.operation_id,
        operation_type=package.operation_type,
        accepted_task=package.accepted_task,
        accepted_plan=package.accepted_plan,
        authority_context=package.authority_context,
        repository_context=package.repository_context,
        analysis_request=package.analysis_request,
        workspace_identity=package.workspace_identity,
        provider_policy=package.provider_policy,
        package_hash=package_sha256(package.canonical_payload),
    )


def package_from_context(
    *,
    operation_id: str,
    operation_type: str,
    accepted_task: Mapping[str, Any],
    accepted_plan: Mapping[str, Any],
    authority_context: Mapping[str, Any],
    repository_context: Mapping[str, Any],
    workspace_identity: Mapping[str, Any],
    provider_policy: Mapping[str, Any],
) -> FrozenAnalysisPackage:
    """Assemble only from an explicitly supplied frozen request.

    The legacy task/plan/context convenience mapping is intentionally not
    converted here; doing so would create a second, ungoverned schema.
    """
    request = repository_context.get("aflow_analysis_request")
    if not isinstance(request, Mapping):
        raise AnalysisPackageError("accepted operation lacks aflow_analysis_request")
    return assemble_analysis_package(
        operation_id=operation_id,
        operation_type=operation_type,
        accepted_task=accepted_task,
        accepted_plan=accepted_plan,
        authority_context=authority_context,
        analysis_request=request,
        repository_context=repository_context,
        workspace_identity=workspace_identity,
        provider_policy=provider_policy,
    )
