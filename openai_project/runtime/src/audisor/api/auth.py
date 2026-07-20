"""Account authentication HTTP endpoints."""

from __future__ import annotations

from functools import lru_cache

from fastapi import APIRouter, Depends, Header, HTTPException

from audisor.schemas.auth import (
    AuthErrorResponse,
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from audisor.service_auth import AuthError, AuthService

router = APIRouter(prefix="/v1/auth", tags=["auth"])


@lru_cache(maxsize=1)
def get_auth_service() -> AuthService:
    return AuthService()


def get_current_user(
    authorization: str | None = Header(None),
    service: AuthService = Depends(get_auth_service),
) -> UserResponse:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid authorization header")
    token = authorization[7:]
    user = service.user_from_token(token)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return user


@router.post(
    "/register",
    response_model=UserResponse,
    responses={409: {"model": AuthErrorResponse, "description": "Username already exists"}},
)
def register(
    request: RegisterRequest,
    service: AuthService = Depends(get_auth_service),
) -> UserResponse:
    try:
        return service.register(request)
    except AuthError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/login",
    response_model=TokenResponse,
    responses={401: {"model": AuthErrorResponse, "description": "Invalid credentials"}},
)
def login(
    request: LoginRequest,
    service: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    try:
        return service.login(request)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.post("/logout", status_code=204)
def logout(
    authorization: str | None = Header(None),
    service: AuthService = Depends(get_auth_service),
) -> None:
    if authorization and authorization.startswith("Bearer "):
        service.logout(authorization[7:])


@router.get("/me", response_model=UserResponse)
def me(user: UserResponse = Depends(get_current_user)) -> UserResponse:
    return user