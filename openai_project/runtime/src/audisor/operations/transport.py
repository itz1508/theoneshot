from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, TextIO

from .models import BuildOperationInput, ClientMetadata, FixOperationInput, OperationRequest
from .store import SharedOperationStore

MAX_REQUEST_BYTES = 1_048_576
EXIT_OK = 0
EXIT_INPUT_ERROR = 2
EXIT_IDENTITY_CONFLICT = 3
EXIT_SERVICE_ERROR = 4
_SECRET = re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*([:=])\s*([^,\s}]+)")


class TransportError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _fix_operation(value: dict[str, Any], operation_id: str) -> FixOperationInput:
    try:
        from audisor_backend.controllers.fix_host import AcceptedFixOperation
        from audisor_backend.schemas.fix.models import Finding, FixScopedManifest, ImplementationPlan, MinorIssue, PlanStep, Statement

        findings = [Finding(**item) for item in value["findings"]]
        manifest = FixScopedManifest(**value["manifest"])
        statements = tuple(Statement(**item) for item in value["statements"])
        plan_value = value["plan"]
        plan = ImplementationPlan(
            steps=[PlanStep(**item) for item in plan_value["steps"]],
            target_files=plan_value["target_files"],
            is_qualified=plan_value["is_qualified"],
            minor_issues=[MinorIssue(**item) for item in plan_value.get("minor_issues", [])],
        )
        operation = AcceptedFixOperation(
            operation_id=operation_id,
            findings=findings,
            manifest=manifest,
            statements=statements,
            plan=plan,
            workspace_identity=value["workspace_identity"],
            authority_context=value["authority_context"],
            aflow_analysis_request=value.get("aflow_analysis_request"),
            authority_decisions=value.get("authority_decisions"),
        )
        return FixOperationInput(operation)
    except (KeyError, TypeError, ValueError, ImportError) as exc:
        if isinstance(exc, ImportError) and "audisor_backend" in str(exc):
            raise TransportError("fix_engine_unavailable", "The audisor_backend Fix engine is not installed.") from exc
        raise TransportError("fix_contract_invalid", "Fix payload is not a valid typed operation") from exc


def deserialize_request(payload: Any) -> OperationRequest:
    if not isinstance(payload, dict):
        raise TransportError("invalid_operation_envelope", "request must be a JSON object")
    allowed = {"operation_id", "operation_kind", "client", "repository", "requested_scope", "build", "fix", "delivery"}
    unknown = set(payload) - allowed
    if unknown:
        raise TransportError("invalid_operation_envelope", "unknown request fields")
    try:
        client_value = payload["client"]
        client = ClientMetadata(
            client_id=client_value["client_id"],
            adapter_id=client_value["adapter_id"],
            adapter_version=client_value["adapter_version"],
            client_version=client_value.get("client_version"),
            session_reference=client_value.get("session_reference"),
            capabilities=tuple(client_value.get("capabilities", ())),
        )
        build_value = payload.get("build")
        fix_value = payload.get("fix")
        build = None
        fix = None
        if build_value is not None:
            if not isinstance(build_value, dict) or set(build_value) != {"build_id", "request"}:
                raise TransportError("build_contract_invalid", "Build payload is incomplete")
            from audisor.schemas.execution import BuildExecutionRequest
            build = BuildOperationInput(build_value["build_id"], BuildExecutionRequest.model_validate(build_value["request"]))
        if fix_value is not None:
            if not isinstance(fix_value, dict):
                raise TransportError("fix_contract_invalid", "Fix payload is malformed")
            fix = _fix_operation(fix_value, payload["operation_id"])
        request = OperationRequest(
            operation_id=payload["operation_id"],
            operation_kind=payload["operation_kind"],
            client=client,
            repository=payload["repository"],
            requested_scope=payload["requested_scope"],
            build=build,
            fix=fix,
            delivery=payload.get("delivery", {}),
        )
        request.validate()
        return request
    except TransportError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise TransportError("invalid_operation_envelope", "request could not be deserialized") from exc


def _public(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _public(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_public(item) for item in value]
    if isinstance(value, str):
        value = _SECRET.sub(r"\1\2[REDACTED]", value)
        if re.match(r"^[A-Za-z]:[\\/]", value) or value.startswith(("/", "\\\\")):
            return "internal://redacted"
    return value


def canonical_response(response: Any) -> str:
    value = response.as_dict() if hasattr(response, "as_dict") else response
    return json.dumps(_public(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def read_request(*, request_file: str | None, stdin: TextIO) -> dict[str, Any]:
    if request_file:
        if not stdin.isatty():
            piped = stdin.buffer.read() if hasattr(stdin, "buffer") else stdin.read().encode("utf-8")
            if piped.strip():
                raise TransportError("multiple_input_sources", "use --request-file or stdin, not both")
        try:
            raw = Path(request_file).read_bytes()
        except OSError as exc:
            raise TransportError("input_unreadable", "request file could not be read") from exc
    else:
        raw = stdin.buffer.read() if hasattr(stdin, "buffer") else stdin.read().encode("utf-8")
    if len(raw) > MAX_REQUEST_BYTES:
        raise TransportError("transport_request_too_large", "request exceeds the 1 MiB limit")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TransportError("invalid_json", "request is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise TransportError("invalid_operation_envelope", "request must be a JSON object")
    return value


def default_operation_service():
    """Construct the existing hosts; no lifecycle logic belongs here."""
    from audisor.api.executions import get_build_executor
    from .service import AcceptedOperationService

    root = Path(os.environ.get("AUDISOR_FIX_DATA_DIR", Path.home() / ".audisor" / "fix-operations"))

    class LazyFixDispatcher:
        def dispatch(self, operation, continue_implementation, finalize_unresolved):
            try:
                from audisor_backend.controllers.fix_host import AcceptedFixDispatcher, FixOperationStore
            except ImportError as exc:
                if "audisor_backend" in str(exc):
                    raise TransportError("fix_engine_unavailable", "The audisor_backend Fix engine is not installed.") from exc
                raise
            return AcceptedFixDispatcher(FixOperationStore(root)).dispatch(operation, continue_implementation, finalize_unresolved)

    return AcceptedOperationService(
        SharedOperationStore(Path(os.environ.get("AUDISOR_OPERATION_DATA_DIR", Path.home() / ".audisor" / "operations"))),
        build_executor=get_build_executor(),
        fix_dispatcher=LazyFixDispatcher(),
        fix_continue=lambda operation, result: result,
        fix_finalize=lambda operation, result: result,
    )


def canonical_operation_service():
    """Construct the host-agnostic canonical operation service.

    Uses AudisorOperationExecutor as the execution core and the new
    AudisorOperationStore for persistence.  This is the production path
    for the host-agnostic runtime; it does not depend on BuildExecutor.

    Fix operations are routed to the existing audisor_backend Fix
    dispatcher via FixRouteConfig so they never enter the generic
    LocalWorker mutation path.
    """
    from audisor.operations import AudisorOperationExecutor
    from audisor.operations.artifacts import ArtifactStore
    from audisor.operations.executor import ExecutorConfig, FixRouteConfig
    from audisor.operations.mutation_enforcer import MutationEnforcer
    from audisor.operations.service import CanonicalOperationService
    from audisor.operations.store import AudisorOperationStore

    data_dir = Path(os.environ.get("AUDISOR_OPERATION_DATA_DIR", Path.home() / ".audisor" / "operations"))
    artifact_dir = Path(os.environ.get("AUDISOR_ARTIFACT_DATA_DIR", Path.home() / ".audisor" / "artifacts"))
    fix_data_dir = Path(os.environ.get("AUDISOR_FIX_DATA_DIR", Path.home() / ".audisor" / "fix-operations"))

    store = AudisorOperationStore(data_dir)
    artifact_store = ArtifactStore(artifact_dir)
    enforcer = MutationEnforcer(base_dir=Path.cwd())

    # Lazy Fix dispatcher: imports audisor_backend only when a Fix
    # request is actually dispatched.
    class _LazyFixDispatcher:
        def dispatch(self, operation, continue_implementation, finalize_unresolved):
            try:
                from audisor_backend.controllers.fix_host import AcceptedFixDispatcher, FixOperationStore
            except ImportError as exc:
                if "audisor_backend" in str(exc):
                    raise TransportError("fix_engine_unavailable", "The audisor_backend Fix engine is not installed.") from exc
                raise
            return AcceptedFixDispatcher(FixOperationStore(fix_data_dir)).dispatch(
                operation, continue_implementation, finalize_unresolved
            )

    # Automatic host continuation: launch Codex after an accepted Fix.
    from audisor.codex.fix_continuation import CodexFixContinuation
    from audisor.codex.fix_verification import FixPostExecutionVerifier

    fix_continuation = CodexFixContinuation(
        launch_result_store_root=data_dir,
        verifier=FixPostExecutionVerifier(),
    )

    fix_route = FixRouteConfig(
        fix_dispatcher=_LazyFixDispatcher(),
        continue_callback=lambda operation, result: result,
        finalize_callback=lambda operation, result: result,
        fix_continuation=fix_continuation,
    )

    executor = AudisorOperationExecutor(
        config=ExecutorConfig(
            operation_store=store,
            artifact_store=artifact_store,
            mutation_enforcer=enforcer,
            fix_route=fix_route,
        )
    )
    return CanonicalOperationService(executor)
