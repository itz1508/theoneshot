"""Authentication boundary: ports and the development adapter."""

from .development import DEV_IDENTITY_HEADER, DevelopmentAuthProvider, current_environment
from .ports import AuthContext, AuthenticationError, AuthProvider

__all__ = [
    "AuthContext",
    "AuthenticationError",
    "AuthProvider",
    "DevelopmentAuthProvider",
    "DEV_IDENTITY_HEADER",
    "current_environment",
]
