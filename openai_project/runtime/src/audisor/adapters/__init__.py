"""Audisor host adapters package.

Provides protocol definitions and concrete adapter implementations
for translating between host-specific request/response formats and
Audisor's canonical operation schemas.
"""

from audisor.adapters.protocol import (
    AudisorOperationRequest,
    AudisorOperationResult,
    HostCapabilities,
    HostRequestAdapter,
    HostResponseAdapter,
)

__all__ = [
    "AudisorOperationRequest",
    "AudisorOperationResult",
    "HostCapabilities",
    "HostRequestAdapter",
    "HostResponseAdapter",
]