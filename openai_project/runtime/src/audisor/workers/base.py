"""Provider-neutral worker contract, capabilities, and normalized failures."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from audisor.schemas.task_input import TaskInput
from audisor.schemas.task_output import TaskOutput


class ProviderCapabilities(BaseModel):
    """Capabilities proven by an adapter; never inferred from a model ID."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    text: bool
    vision: bool = False
    tool_calls: bool = False
    structured_output: bool = False
    streaming: bool = False
    terminable_execution: bool = False
    native_json_schema: bool = False


class ProviderError(RuntimeError):
    """Base normalized provider failure with bounded internal evidence."""

    code = "provider_unavailable"

    def __init__(self, message: str, *, internal_detail: str = "") -> None:
        super().__init__(message)
        self.internal_detail = internal_detail[:1000]


class ProviderConfigurationError(ProviderError):
    code = "provider_configuration_error"


class ProviderUnavailableError(ProviderError):
    code = "provider_unavailable"


class ProviderAuthenticationError(ProviderError):
    code = "provider_authentication_error"


class ProviderRateLimitedError(ProviderError):
    code = "provider_rate_limited"


class ProviderTimeoutError(ProviderError):
    code = "provider_timeout"


class ProviderInvalidResponseError(ProviderError):
    code = "provider_invalid_response"


class ProviderResponseNotJsonError(ProviderInvalidResponseError):
    code = "provider_response_not_json"


class ProviderContractInvalidError(ProviderInvalidResponseError):
    code = "provider_contract_invalid"


class ProviderSchemaUnsupportedError(ProviderError):
    code = "provider_schema_unsupported"


class ProviderSchemaRequestRejectedError(ProviderError):
    code = "provider_schema_request_rejected"


class ProviderResponseTooLargeError(ProviderError):
    code = "provider_response_too_large"


class ProviderProcessReapError(ProviderError):
    code = "provider_process_reap_failed"


class StageBudgetExhaustedError(ProviderError):
    code = "stage_budget_exhausted"


class ProviderPermanentRequestError(ProviderError):
    code = "provider_permanent_request_error"


class ProviderCapabilityError(ProviderError):
    code = "provider_capability_unsupported"


@runtime_checkable
class WorkerProvider(Protocol):
    """Typed provider boundary consumed by provider-neutral core services."""

    provider_id: str

    def configuration_status(self) -> bool:
        """Return whether the adapter's required namespaced config is present."""
        ...

    def capabilities(self) -> ProviderCapabilities:
        """Return adapter-proven capabilities without consulting model names."""
        ...

    def execute(self, task: TaskInput) -> TaskOutput:
        """Execute one complete typed task and return the normalized envelope."""
        ...
