"""Authenticated, workspace-scoped A-Flow management facade."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from audisor.audisor_lifecycle.management import (
    get_issue,
    link_issue_retry,
    list_issue_events,
    list_issues,
    provider_status,
    workspace_identity,
)
from audisor.audisor_lifecycle.persistence import default_state_root

from ..auth.ports import AuthContext
from .dependencies import get_auth_context
from .operations_routes import _get_controller


class RetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_version: int = Field(ge=0)
    idempotency_key: str = Field(min_length=8, max_length=128)


aflow_router = APIRouter(prefix="/v1/aflow", tags=["aflow-management"])


def _assert_workspace(auth: AuthContext) -> None:
    expected = workspace_identity(default_state_root())
    if auth.workspace_id is None:
        raise HTTPException(
            status_code=403,
            detail={
                "issue_code": "workspace_identity_required",
                "direct_cause": "Authenticated request has no workspace identity.",
            },
        )
    if auth.workspace_id != expected:
        raise HTTPException(
            status_code=409,
            detail={
                "issue_code": "provider_configuration_error",
                "direct_cause": "The authenticated workspace does not match the configured A-Flow state root.",
                "expected_workspace_id": expected,
            },
        )


@aflow_router.get("/status")
def aflow_status(auth: AuthContext = Depends(get_auth_context)) -> dict[str, Any]:
    _assert_workspace(auth)
    return provider_status(probe=False)


@aflow_router.post("/provider-probe")
def aflow_provider_probe(auth: AuthContext = Depends(get_auth_context)) -> dict[str, Any]:
    _assert_workspace(auth)
    return provider_status(probe=True)


@aflow_router.get("/issues")
def aflow_issues(
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    auth: AuthContext = Depends(get_auth_context),
) -> dict[str, Any]:
    _assert_workspace(auth)
    return list_issues(cursor=cursor, limit=limit)


@aflow_router.get("/issues/{issue_id}")
def aflow_issue(
    issue_id: str,
    auth: AuthContext = Depends(get_auth_context),
) -> dict[str, Any]:
    _assert_workspace(auth)
    issue = get_issue(issue_id)
    if issue is None:
        raise HTTPException(status_code=404, detail="issue_not_found")
    return issue


@aflow_router.get("/issue-events")
def aflow_issue_events(
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    auth: AuthContext = Depends(get_auth_context),
) -> dict[str, Any]:
    _assert_workspace(auth)
    return list_issue_events(after=after, limit=limit)


def _retry(issue_id: str, payload: RetryRequest, provider: Literal["local-openai-compatible", "fireworks"]):
    issue = get_issue(issue_id)
    if issue is None:
        raise HTTPException(status_code=404, detail="issue_not_found")
    retry = issue.get("retry", {})
    operation_id = issue.get("operation_id")
    if not retry.get("supported") or not operation_id:
        raise HTTPException(
            status_code=409,
            detail={
                "reason": "retry_not_owned_by_operation_controller",
                "instruction": retry.get("instruction"),
            },
        )
    if provider == "fireworks" and not provider_status(probe=False)["fallback"]["ready"]:
        raise HTTPException(
            status_code=409,
            detail={
                "reason": "fireworks_not_ready",
                "missing_prerequisite": "Complete explicit fallback configuration and a valid structured-output probe.",
            },
        )
    result = _get_controller().retry_aflow(
        operation_id,
        operation_version=payload.operation_version,
        idempotency_key=payload.idempotency_key,
        provider=provider,
    )
    if result.detail.get("error"):
        raise HTTPException(status_code=409, detail=result.detail)
    link_issue_retry(
        issue_id,
        lifecycle_run_id=result.detail.get("lifecycle_run_id"),
        provider=provider,
    )
    return {
        "operation_id": result.operation_id,
        "state": result.state.value,
        "detail": result.detail,
    }


@aflow_router.post("/issues/{issue_id}/retry-local")
def retry_local(
    issue_id: str,
    payload: RetryRequest,
    auth: AuthContext = Depends(get_auth_context),
):
    _assert_workspace(auth)
    return _retry(issue_id, payload, "local-openai-compatible")


@aflow_router.post("/issues/{issue_id}/continue-with-fallback")
def continue_with_fallback(
    issue_id: str,
    payload: RetryRequest,
    auth: AuthContext = Depends(get_auth_context),
):
    _assert_workspace(auth)
    return _retry(issue_id, payload, "fireworks")
