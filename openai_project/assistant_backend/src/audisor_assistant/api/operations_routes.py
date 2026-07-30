"""Authenticated OperationController HTTP facade."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from operation_controller.adapters.review import CanonicalAflowReviewAdapter
from operation_controller.controller import FileOperationStore, OperationController
from operation_controller.event_store import EventStore

from audisor.audisor_lifecycle.persistence import default_state_root

from ..auth.ports import AuthContext
from .dependencies import get_auth_context


class CreateOperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(min_length=1)
    source_kind: Literal["task", "prepared_plan", "fix"] = "task"
    plan: dict[str, Any] | None = None


class ResumeOperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resume_type: str
    suspension_id: str
    operation_version: int
    payload: dict[str, Any]


class ClaimToolExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claimant_id: str
    suspension_id: str
    call_id: str
    argument_digest: str


class ClaimToolExecutionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str
    state: str
    claimed: bool
    claimant_id: str
    call_id: str
    tool_name: str
    arguments: dict[str, Any]
    argument_digest: str
    suspension_id: str
    idempotent: bool = False


operations_router = APIRouter(prefix="/v1/operations", tags=["operations"])
_controller: OperationController | None = None
_event_store: EventStore | None = None


def _state_root() -> Path:
    return default_state_root()


def _get_event_store() -> EventStore:
    global _event_store
    if _event_store is None:
        _event_store = EventStore(_state_root().parent / "operations")
    return _event_store


def _get_controller() -> OperationController:
    global _controller
    if _controller is None:
        operation_root = _state_root().parent / "operations"
        _controller = OperationController(
            store=FileOperationStore(operation_root),
            event_store=_get_event_store(),
            review_adapter=CanonicalAflowReviewAdapter(state_root=_state_root()),
        )
    return _controller


def _result_body(result) -> dict[str, Any]:
    return {
        "operation_id": result.operation_id,
        "state": result.state.value,
        "detail": result.detail,
    }


@operations_router.post("")
def create_operation(
    payload: CreateOperationRequest,
    auth: AuthContext = Depends(get_auth_context),
) -> dict[str, Any]:
    result = _get_controller().accept(
        payload.source_kind,
        payload.prompt,
        plan=payload.plan,
        context={"workspace_id": auth.workspace_id},
    )
    return _result_body(result)


@operations_router.get("/{operation_id}/status")
def operation_status(
    operation_id: str,
    auth: AuthContext = Depends(get_auth_context),
) -> dict[str, Any]:
    result = _get_controller().status(operation_id)
    if result.detail.get("error") == "operation_not_found":
        raise HTTPException(status_code=404, detail="operation_not_found")
    return _result_body(result)


@operations_router.post("/{operation_id}/cancel")
def cancel_operation(
    operation_id: str,
    auth: AuthContext = Depends(get_auth_context),
) -> dict[str, Any]:
    result = _get_controller().cancel(operation_id)
    if result.detail.get("error") == "operation_not_found":
        raise HTTPException(status_code=404, detail="operation_not_found")
    return _result_body(result)


@operations_router.post("/{operation_id}/resume")
def resume_operation(
    operation_id: str,
    payload: ResumeOperationRequest,
    auth: AuthContext = Depends(get_auth_context),
) -> dict[str, Any]:
    result = _get_controller().resume(
        operation_id,
        resume_input={
            **payload.payload,
            "resume_type": payload.resume_type,
            "suspension_id": payload.suspension_id,
            "operation_version": payload.operation_version,
        },
    )
    if result.detail.get("error"):
        status_code = 404 if result.detail["error"] == "operation_not_found" else 409
        raise HTTPException(status_code=status_code, detail=result.detail)
    return _result_body(result)


@operations_router.post("/{operation_id}/claim")
def claim_tool_execution(
    operation_id: str,
    payload: ClaimToolExecutionRequest,
    auth: AuthContext = Depends(get_auth_context),
) -> dict[str, Any]:
    result = _get_controller().claim_tool_execution(
        operation_id,
        claimant_id=payload.claimant_id,
        suspension_id=payload.suspension_id,
        call_id=payload.call_id,
        argument_digest=payload.argument_digest,
    )
    if result.detail.get("error"):
        status_code = 404 if result.detail["error"] == "operation_not_found" else 409
        raise HTTPException(status_code=status_code, detail=result.detail)
    return {
        "operation_id": result.operation_id,
        "state": result.state.value,
        **result.detail,
    }


@operations_router.get("/{operation_id}/events")
def operation_events(
    operation_id: str,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    auth: AuthContext = Depends(get_auth_context),
) -> dict[str, Any]:
    controller_result = _get_controller().status(operation_id)
    if controller_result.detail.get("error") == "operation_not_found":
        raise HTTPException(status_code=404, detail="operation_not_found")
    events = _get_event_store().read_after(operation_id, after=after, limit=limit)
    cursor = events[-1].sequence if events else after
    return {
        "operation_id": operation_id,
        "events": [event.__dict__ for event in events],
        "cursor": cursor,
        "terminal": controller_result.state.value in {
            "completed",
            "cancelled_by_user",
            "superseded",
        },
    }
