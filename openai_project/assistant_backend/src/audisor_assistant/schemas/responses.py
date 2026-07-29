"""Public response envelope for POST /v1/assistant/requests.

The envelope never carries provider credentials, raw provider payloads,
stack traces, hidden prompts, or filesystem paths.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..domain.modes import AssistantMode


class AssistantStatus(str, Enum):
    COMPLETED = "completed"
    UNCERTAINTY = "uncertainty"
    FAILED = "failed"


class PublicErrorCategory(str, Enum):
    CONFIGURATION = "configuration"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    AUTHENTICATION = "authentication"
    RATE_LIMITED = "rate_limited"
    INVALID_RESPONSE = "invalid_response"
    UNSUPPORTED = "unsupported"
    INTERNAL = "internal"


class ProviderInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source: str  # "local" | "cloud"


class AssistantResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    mode: AssistantMode
    status: AssistantStatus
    result: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    provider: ProviderInfo | None = None
    usage: dict[str, int] | None = None
    # Execution metadata (fix_wording engine selection). ``provider`` stays
    # provider identity only — fallback state is never encoded into it.
    engine: str | None = None  # "model" | "languagetool"
    fallback_used: bool = False
    fallback_reason: str = ""
    uncertainty: list[str] = Field(default_factory=list)


class AssistantModelsResponse(BaseModel):
    """Envelope for GET /v1/assistant/models.

    Never carries credentials or raw provider payloads.  ``reachable``
    is null for providers that are not probed (cloud).
    """

    model_config = ConfigDict(extra="forbid")

    provider: ProviderInfo
    current_model: str
    available_models: list[str] = Field(default_factory=list)
    reachable: bool | None = None


class AssistantHealthResponse(BaseModel):
    """Envelope for GET /v1/assistant/health (unauthenticated liveness)."""

    model_config = ConfigDict(extra="forbid")

    status: str = "ok"
    provider: ProviderInfo | None = None
