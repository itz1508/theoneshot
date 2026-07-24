"""Account authentication HTTP endpoints — tombstoned in 0.10.0.

Factory and dependency functions are retained with lazy imports for
direct Python API compatibility.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/v1/auth", tags=["auth"])

_DEPRECATION_BODY = {
    "code": "legacy_runtime_deprecated",
    "message": "The Audisor model-execution runtime is deprecated.",
    "removal_version": "1.0.0",
}


@router.post("/register", deprecated=True, responses={410: {"description": "Legacy runtime deprecated"}})
def register() -> JSONResponse:
    return JSONResponse(status_code=410, content=_DEPRECATION_BODY)


@router.post("/login", deprecated=True, responses={410: {"description": "Legacy runtime deprecated"}})
def login() -> JSONResponse:
    return JSONResponse(status_code=410, content=_DEPRECATION_BODY)


@router.post("/logout", deprecated=True, responses={410: {"description": "Legacy runtime deprecated"}})
def logout() -> JSONResponse:
    return JSONResponse(status_code=410, content=_DEPRECATION_BODY)


@router.get("/me", deprecated=True, responses={410: {"description": "Legacy runtime deprecated"}})
def me() -> JSONResponse:
    return JSONResponse(status_code=410, content=_DEPRECATION_BODY)


# ---------------------------------------------------------------------------
# Legacy compatibility factories — lazy imports keep the module load-free
# of the legacy dependency graph.
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def get_auth_service() -> Any:
    from audisor.service_auth import AuthService

    return AuthService()


def get_current_user(authorization: str | None = Header(None)) -> Any:
    """Legacy dependency — retained for direct Python API compatibility."""
    from fastapi import HTTPException

    from audisor.service_auth import AuthService

    service = AuthService()
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid authorization header")
    token = authorization[7:]
    user = service.user_from_token(token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return user
