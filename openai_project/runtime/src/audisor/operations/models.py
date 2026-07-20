from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, is_dataclass
from hashlib import sha256
from typing import Any, Literal, Mapping

from audisor.schemas.execution import BuildExecutionRequest

OperationKind = Literal["build", "fix"]


class OperationValidationError(ValueError):
    pass


class OperationIdentityConflict(ValueError):
    code = "operation_identity_conflict"


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if hasattr(value, "__dict__"):
        return {str(key): _jsonable(item) for key, item in vars(value).items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def canonical_request_hash(value: Any) -> str:
    payload = json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ClientMetadata:
    client_id: str
    adapter_id: str
    adapter_version: str
    client_version: str | None = None
    session_reference: str | None = None
    capabilities: tuple[str, ...] = ()

    def validate(self) -> None:
        if not self.client_id.strip() or not self.adapter_id.strip() or not self.adapter_version.strip():
            raise OperationValidationError("client_identity_invalid")


@dataclass(frozen=True)
class BuildOperationInput:
    build_id: str
    request: BuildExecutionRequest

    def validate(self, operation_id: str) -> None:
        if self.request.execution_id != operation_id:
            raise OperationValidationError("build execution_id must equal operation_id")
        if not self.build_id.strip():
            raise OperationValidationError("build_contract_invalid")


@dataclass(frozen=True)
class FixOperationInput:
    operation: Any

    def validate(self, operation_id: str) -> None:
        if getattr(self.operation, "operation_id", None) != operation_id:
            raise OperationValidationError("fix operation_id must equal operation_id")
        if not getattr(self.operation, "findings", None):
            raise OperationValidationError("fix_findings_missing")


@dataclass(frozen=True)
class OperationRequest:
    operation_id: str
    operation_kind: OperationKind
    client: ClientMetadata
    repository: Mapping[str, Any]
    requested_scope: Mapping[str, Any]
    build: BuildOperationInput | None = None
    fix: FixOperationInput | None = None
    delivery: Mapping[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        self.client.validate()
        if not self.operation_id.strip() or self.operation_kind not in ("build", "fix"):
            raise OperationValidationError("invalid_operation_envelope")
        if (self.build is None) == (self.fix is None):
            raise OperationValidationError("operation payloads must be mutually exclusive")
        if self.operation_kind == "build":
            if self.fix is not None:
                raise OperationValidationError("Build request contains Fix payload")
            assert self.build is not None
            self.build.validate(self.operation_id)
        else:
            if self.build is not None:
                raise OperationValidationError("Fix request contains Build payload")
            assert self.fix is not None
            self.fix.validate(self.operation_id)
        forbidden = {"continuation", "authority", "aflow_enabled", "provider", "model"}
        if forbidden & set(self.delivery):
            raise OperationValidationError("forbidden operation input field")

    def canonical_hash(self) -> str:
        self.validate()
        return canonical_request_hash(self)


@dataclass(frozen=True)
class OperationResponse:
    operation_id: str
    operation_kind: OperationKind
    client_id: str
    request_hash: str
    status: Literal["accepted", "blocked", "failed", "completed", "existing"]
    aflow_enabled: bool | None
    aflow_invoked: bool | None
    decision_state: str | None
    execution_contract_reference: Any = None
    artifact_references: tuple[Any, ...] = ()
    authority_limits: Mapping[str, Any] = field(default_factory=dict)
    continuation: Mapping[str, Any] = field(default_factory=dict)
    failure: Mapping[str, Any] | None = None
    existing_result: bool = False

    def as_dict(self) -> dict[str, Any]:
        return _jsonable(self)
