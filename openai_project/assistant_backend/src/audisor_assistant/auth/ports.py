"""Authentication ports.

No production authentication vendor has been selected for this product.
This module defines only the interface boundary; production account
integration stops here until a vendor decision is made.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol, runtime_checkable


class AuthenticationError(Exception):
    """Raised when a request cannot be authenticated.  The message is
    safe for public display."""


@dataclass(frozen=True)
class AuthContext:
    """Authenticated request context."""

    subject: str
    environment: str  # "development" | "test" | "production"
    workspace_id: str | None = None


@runtime_checkable
class AuthProvider(Protocol):
    """Protocol for authenticating an incoming request from its headers."""

    def authenticate(self, headers: Mapping[str, str]) -> AuthContext:
        """Return an AuthContext or raise AuthenticationError."""
        ...
