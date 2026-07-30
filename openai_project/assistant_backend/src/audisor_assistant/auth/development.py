"""Development/test authentication adapter.

Accepts a caller-declared identity header only when the server runs in
``development`` or ``test`` mode.  In ``production`` mode every request is
rejected: no production authentication vendor has been selected, and this
adapter must never masquerade as one.

Derives workspace_id from the configured state root so that workspace
isolation checks have a concrete identity to compare.
"""
from __future__ import annotations

import os
from typing import Mapping

from .ports import AuthContext, AuthenticationError, AuthProvider

#: Header carrying the development identity.
DEV_IDENTITY_HEADER = "x-audisor-dev-user"

#: Environment variable naming the deployment environment.
ENVIRONMENT_VAR = "AUDISOR_ASSISTANT_ENV"

_ALLOWED_DEV_ENVIRONMENTS = frozenset({"development", "test"})


def current_environment() -> str:
    return os.environ.get(ENVIRONMENT_VAR, "development").strip().lower()


class DevelopmentAuthProvider(AuthProvider):
    def __init__(self, environment: str | None = None) -> None:
        self._environment = environment

    @property
    def environment(self) -> str:
        return self._environment or current_environment()

    def authenticate(self, headers: Mapping[str, str]) -> AuthContext:
        environment = self.environment
        if environment not in _ALLOWED_DEV_ENVIRONMENTS:
            raise AuthenticationError(
                "Production authentication is not configured. "
                "A production authentication provider has not been selected."
            )
        normalized = {key.lower(): value for key, value in headers.items()}
        subject = normalized.get(DEV_IDENTITY_HEADER, "").strip()
        if not subject:
            raise AuthenticationError(
                "Missing development identity header."
            )
        from audisor.audisor_lifecycle.persistence import default_state_root
        from audisor.audisor_lifecycle.management import workspace_identity
        return AuthContext(
            subject=subject,
            environment=environment,
            workspace_id=workspace_identity(default_state_root()),
        )
