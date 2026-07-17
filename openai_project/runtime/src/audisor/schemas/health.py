"""Provider-neutral liveness and readiness response contracts."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    status: Literal["ok"] = "ok"


class ProviderReadiness(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    selected: str | None
    configuration: Literal["present", "missing", "invalid"]
    capabilities_loaded: bool


class ReadinessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    status: Literal["ready", "degraded"]
    provider: ProviderReadiness
    data_root_ready: bool
    schemas_ready: bool
