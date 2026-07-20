from __future__ import annotations

from typing import Any, Callable, Literal
from pathlib import Path

from audisor.adapters.protocol import AudisorOperationRequest
from audisor.config.host_profiles import load_audisor_config
from audisor.operations import AudisorOperationExecutor
from audisor.operations.artifacts import ArtifactStore
from audisor.operations.mutation_enforcer import MutationEnforcer
from audisor.operations.result import AudisorOperationResult
from audisor.operations.store import AudisorOperationStore
from audisor.schemas.authority import AuthorityContext, AuthoritySource, PermissionSet
from audisor.schemas.idempotency import IdempotencyContext

from .mappers.build import map_build
from .mappers.fix import map_fix
from .models import OperationRequest, OperationResponse, OperationValidationError
from .store import SharedOperationStore


def _host_status(result: Any) -> str:
    status = result.get("status") if isinstance(result, dict) else getattr(result, "status", None)
    if status in {"accepted", "blocked", "failed", "completed", "existing"}:
        return status
    if status in {"rejected", "validation_failed", "provider_failed"}:
        return "blocked" if status == "rejected" else "failed"
    return "completed"


class AcceptedOperationService:
    """Common envelope, identity index, and response normalization only."""

    def __init__(self, store: SharedOperationStore, *, build_executor: Any, fix_dispatcher: Any, fix_continue: Callable, fix_finalize: Callable):
        self.store = store
        self.build_executor = build_executor
        self.fix_dispatcher = fix_dispatcher
        self.fix_continue = fix_continue
        self.fix_finalize = fix_finalize

    def accept(self, request: OperationRequest) -> OperationResponse:
        request.validate()
        request_hash = request.canonical_hash()
        existing = self.store.load(request.operation_id)
        if existing is not None:
            if existing.get("canonical_request_hash") != request_hash:
                from .models import OperationIdentityConflict
                raise OperationIdentityConflict("operation_id is already bound to a different request")
            stored = existing.get("stored_response")
            if stored is None:
                raise OperationValidationError("operation_persistence_failed")
            return OperationResponse(**{**stored, "status": "existing", "existing_result": True})

        if request.operation_kind == "build":
            build_id, build_input = map_build(request)
            host_identity = {"build_id": build_id, "execution_id": build_input.request.execution_id}
        else:
            fix_input = map_fix(request)
            host_identity = {"fix_operation_id": fix_input.operation.operation_id}
        client_metadata = {
            "client_id": request.client.client_id,
            "adapter_id": request.client.adapter_id,
            "adapter_version": request.client.adapter_version,
            "client_version": request.client.client_version,
            "session_reference": request.client.session_reference,
            "capabilities": list(request.client.capabilities),
        }
        existing = self.store.bind(request.operation_id, request_hash, {"operation_kind": request.operation_kind, "host_identity": host_identity, "client_metadata": client_metadata})
        if existing is not None:
            stored = existing.get("stored_response")
            if stored is None:
                raise OperationValidationError("operation_persistence_failed")
            return OperationResponse(**{**stored, "status": "existing", "existing_result": True})
        try:
            if request.operation_kind == "build":
                host_result = self.build_executor.execute(build_id, build_input.request)
            else:
                host_result = self.fix_dispatcher.dispatch(fix_input.operation, self.fix_continue, self.fix_finalize)
        except Exception as exc:
            response = self._normalize(request, request_hash, {"status": "failed", "failure": {"code": getattr(exc, "code", "host_failed"), "message": str(exc)}})
            self.store.persist_response(request.operation_id, response.as_dict())
            return response
        response = self._normalize(request, request_hash, host_result)
        self.store.persist_response(request.operation_id, response.as_dict())
        return response

    def _normalize(self, request: OperationRequest, request_hash: str, result: Any) -> OperationResponse:
        status = _host_status(result)
        execution_contract_reference = result.get("execution_contract_reference") if isinstance(result, dict) else None
        authority_limits = result.get("authority_limits", {}) if isinstance(result, dict) else {}
        continuation_permitted = status == "accepted"
        if request.operation_kind == "build" and status == "completed" and request.build is not None:
            build_id = request.build.build_id
            try:
                build_path = self.build_executor.loader.store.build_path(build_id)
                execution = build_path / "executions" / request.build.request.execution_id
                candidates = (
                    execution / "workspace" / "audisor-artifacts" / "execution-contract.json",
                    execution / "evidence" / "aflow-operation-result.json",
                )
                reference = next((candidate for candidate in candidates if candidate.is_file()), None)
                if reference is not None:
                    execution_contract_reference = str(Path(reference).resolve())
                    status = "accepted"
                    continuation_permitted = True
                    authority_limits = authority_limits or dict(request.repository.get("authority_limits", {}))
            except (AttributeError, OSError, ValueError):
                pass
        return OperationResponse(
            operation_id=request.operation_id,
            operation_kind=request.operation_kind,
            client_id=request.client.client_id,
            request_hash=request_hash,
            status=status,
            aflow_enabled=None,
            aflow_invoked=None,
            decision_state=(result.get("decision_state") if isinstance(result, dict) else None),
            execution_contract_reference=execution_contract_reference,
            artifact_references=tuple(result.get("artifact_references", ())) if isinstance(result, dict) else (),
            authority_limits=authority_limits,
            continuation={"state": "permitted" if continuation_permitted else "completed" if status == "completed" else "blocked" if status == "blocked" else "failed", "permitted": continuation_permitted},
            failure=(result.get("failure") if isinstance(result, dict) else None),
        )


class CanonicalOperationService:
    """Host-agnostic operation service backed by AudisorOperationExecutor.

    Translates legacy OperationRequest envelopes into canonical
    AudisorOperationRequest objects, executes them through the canonical
    executor, and normalizes the canonical result back into the legacy
    OperationResponse shape so existing CLI and API consumers remain
    compatible while the runtime moves onto the host-agnostic core.
    """

    def __init__(
        self,
        executor: AudisorOperationExecutor,
        *,
        response_adapter_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self._executor = executor
        self._response_adapter_factory = response_adapter_factory

    def accept(self, request: OperationRequest) -> OperationResponse:
        """Accept a legacy operation request through the canonical executor."""
        request.validate()
        canonical_request = self._to_canonical_request(request)
        canonical_result = self._executor.execute(canonical_request)
        return self._to_operation_response(request, canonical_result)

    def _to_canonical_request(self, request: OperationRequest) -> AudisorOperationRequest:
        """Translate a legacy OperationRequest to a canonical request."""
        # Derive adapter identity from client metadata
        adapter_id = request.client.adapter_id
        host_identity = adapter_id if adapter_id in {"codex", "mcp", "cli", "responses_compatible"} else "unknown"

        # Map adapter identity to a valid AuthoritySource.source_type literal
        source_type: Literal["user", "host_adapter", "system_policy", "mcp_server", "codex"]
        if host_identity == "codex":
            source_type = "codex"
        elif host_identity == "mcp":
            source_type = "mcp_server"
        elif host_identity == "responses_compatible":
            source_type = "host_adapter"
        else:
            source_type = "user"

        # Build authority from repository/requested_scope or defaults
        authority_data = dict(request.requested_scope)
        authority_data.update(request.repository)
        permissions = PermissionSet(
            allowed_paths=authority_data.get("allowed_paths", ["."]),
            prohibited_paths=authority_data.get("prohibited_paths", [".git", ".codex"]),
            allowed_tools=authority_data.get("allowed_tools", []),
            prohibited_tools=authority_data.get("prohibited_tools", []),
        )
        authority = AuthorityContext(
            source=AuthoritySource(
                source_type=source_type,
                grant_id=request.client.client_id,
                host_identity=host_identity,
            ),
            permissions=permissions,
            scope=authority_data.get("scope", "repository"),
        )

        # Build canonical request payload
        canonical_request_payload: dict[str, Any] = {
            "operation_id": request.operation_id,
            "operation_kind": request.operation_kind,
            "client": {
                "client_id": request.client.client_id,
                "adapter_id": request.client.adapter_id,
                "adapter_version": request.client.adapter_version,
            },
            "repository": dict(request.repository),
            "requested_scope": dict(request.requested_scope),
        }
        if request.build is not None:
            canonical_request_payload["build"] = {
                "build_id": request.build.build_id,
                "request": request.build.request.model_dump(mode="json"),
            }
        if request.fix is not None:
            canonical_request_payload["fix"] = self._serialize_fix_operation(request.fix.operation, request.operation_id)

        # Idempotency from delivery or operation_id
        idempotency = None
        delivery = dict(request.delivery)
        if "idempotency_key" in delivery:
            from audisor.schemas.idempotency import IdempotencyKey
            idempotency = IdempotencyContext.from_request(
                IdempotencyKey(
                    key=delivery["idempotency_key"],
                    scope=delivery.get("idempotency_scope", "operation"),
                ),
                request.operation_id,
            )

        return AudisorOperationRequest(
            operation_id=request.operation_id,
            mode=request.operation_kind,
            request=canonical_request_payload,
            authority=authority,
            constraints={},
            host_capabilities=self._host_capabilities(host_identity),
            host_context={
                "adapter": host_identity,
                "client_id": request.client.client_id,
                "client_version": request.client.client_version,
                "session_reference": request.client.session_reference,
            },
            idempotency=idempotency,
        )

    def _host_capabilities(self, host_identity: str) -> Any:
        """Return host capabilities for the derived host identity."""
        from audisor.adapters.codex import CodexCapabilities
        from audisor.adapters.cli import CLICapabilities
        from audisor.adapters.mcp import MCPCapabilities
        from audisor.adapters.responses_compatible import ResponsesCompatibleCapabilities

        if host_identity == "codex":
            return CodexCapabilities()
        if host_identity == "mcp":
            return MCPCapabilities()
        if host_identity == "responses_compatible":
            return ResponsesCompatibleCapabilities()
        return CLICapabilities()

    def _serialize_fix_operation(self, operation: Any, fallback_operation_id: str) -> dict[str, Any]:
        """Serialize a Fix operation into a JSON-compatible dictionary.

        Preserves the complete accepted Fix package including findings,
        manifest, statements, plan, workspace_identity, authority_context,
        and the optional aflow_analysis_request compatibility field.
        """
        from dataclasses import asdict, is_dataclass

        def _jsonable(value: Any) -> Any:
            if hasattr(value, "model_dump"):
                return value.model_dump(mode="json")
            if is_dataclass(value):
                return {key: _jsonable(item) for key, item in asdict(value).items()}
            if isinstance(value, dict):
                return {str(key): _jsonable(item) for key, item in value.items()}
            if isinstance(value, (list, tuple)):
                return [_jsonable(item) for item in value]
            return value

        operation_id = getattr(operation, "operation_id", fallback_operation_id)
        findings = getattr(operation, "findings", [])
        manifest = getattr(operation, "manifest", None)
        statements = getattr(operation, "statements", [])
        plan = getattr(operation, "plan", None)
        workspace_identity = getattr(operation, "workspace_identity", {})
        authority_context = getattr(operation, "authority_context", {})
        aflow_analysis_request = getattr(operation, "aflow_analysis_request", None)

        return {
            "operation_id": operation_id,
            "findings": _jsonable(findings),
            "manifest": _jsonable(manifest) if manifest is not None else None,
            "statements": _jsonable(statements),
            "plan": _jsonable(plan) if plan is not None else None,
            "workspace_identity": _jsonable(workspace_identity),
            "authority_context": _jsonable(authority_context),
            "aflow_analysis_request": _jsonable(aflow_analysis_request) if aflow_analysis_request is not None else None,
        }

    def _to_operation_response(
        self,
        request: OperationRequest,
        result: AudisorOperationResult,
    ) -> OperationResponse:
        """Translate a canonical result back to the legacy OperationResponse."""
        status = result.status
        if status not in ("accepted", "blocked", "failed", "completed", "existing"):
            status = "completed"

        continuation_permitted = status in ("accepted", "completed")

        return OperationResponse(
            operation_id=result.operation_id,
            operation_kind=request.operation_kind,
            client_id=request.client.client_id,
            request_hash=request.canonical_hash(),
            status=status,
            aflow_enabled=None,
            aflow_invoked=None,
            decision_state=None,
            execution_contract_reference=(
                result.execution.get("receipt_id") if result.execution else None
            ),
            artifact_references=tuple(dict(a) for a in result.artifacts),
            authority_limits={},
            continuation={
                "state": "permitted" if continuation_permitted else "blocked",
                "permitted": continuation_permitted,
            },
            failure=result.error.model_dump(mode="json") if result.error else None,
        )
