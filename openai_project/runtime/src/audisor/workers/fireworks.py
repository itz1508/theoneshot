"""Fireworks provider adapter; all Fireworks HTTP details stay here."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import requests

from audisor.schemas.task_input import TaskInput
from audisor.schemas.task_output import TaskOutput
from audisor.workers.base import (
    ProviderAuthenticationError,
    ProviderCapabilityError,
    ProviderCapabilities,
    ProviderConfigurationError,
    ProviderInvalidResponseError,
    ProviderError,
    ProviderPermanentRequestError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderProcessReapError,
    ProviderResponseTooLargeError,
    StageBudgetExhaustedError,
)
from audisor.workers.isolated_http import (
    IsolatedHttpBudgetError,
    IsolatedHttpError,
    IsolatedHttpReapError,
    IsolatedHttpResponseTooLarge,
    IsolatedHttpTimeout,
    isolated_post,
)


class ResponseLike(Protocol):
    status_code: int

    def json(self) -> Any:
        ...


RequestFunction = Callable[..., ResponseLike]
SleepFunction = Callable[[float], None]


@dataclass
class FireworksWorker:
    """Translate typed Audisor tasks to and from an explicit HTTP endpoint.

    The endpoint URL must be provided explicitly — no default server,
    base URL, or path is inferred. A blank endpoint means unconfigured.
    """

    api_key: str = field(repr=False)
    endpoint_url: str
    model: str
    max_attempts: int = 2
    retry_delay_seconds: float = 0.25
    timeout_seconds: float = 300.0
    max_tokens: int = 4096
    request: RequestFunction = field(default=isolated_post, repr=False)
    sleep: SleepFunction = field(default=time.sleep, repr=False)

    provider_id = "fireworks"
    transient_status_codes = frozenset({500, 502, 503, 504})

    def __post_init__(self) -> None:
        pass

    @classmethod
    def from_environment(cls) -> "FireworksWorker":
        return cls(
            api_key=os.environ.get("FIREWORKS_API_KEY", ""),
            endpoint_url=os.environ.get("FIREWORKS_BASE_URL", ""),
            model=os.environ.get("FIREWORKS_MODEL", ""),
        )

    def configuration_status(self) -> bool:
        return all(value.strip() for value in (self.api_key, self.endpoint_url, self.model))

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            text=True,
            terminable_execution=self.request is isolated_post,
        )

    @property
    def schema_mode(self) -> str:
        return "prompt_validated_json"

    def readiness_identity(self) -> dict[str, Any]:
        return {
            "provider_protocol": "fireworks-completions",
            "normalized_endpoint": self.endpoint_url.rstrip("/"),
            "model_id": self.model,
            "structured_output_mode": self.schema_mode,
            "adapter_identity": f"{type(self).__module__}.{type(self).__qualname__}",
            "adapter_version": "2",
            "behavior": {
                "max_tokens": self.max_tokens,
                "max_attempts": self.max_attempts,
                "retry_delay_seconds": self.retry_delay_seconds,
                "timeout_seconds": self.timeout_seconds,
                "credential_present": bool(self.api_key.strip()),
                "terminable_execution": self.request is isolated_post,
            },
        }

    def run_full_readiness_probe(self) -> None:
        output = self.execute(
            TaskInput(
                task_id="aflow-provider-readiness",
                prompt='Return only {"aflow_provider_probe":"ready"}.',
            )
        )
        if output.answer.strip() != '{"aflow_provider_probe":"ready"}':
            raise ProviderInvalidResponseError("Selected provider failed its structured readiness proof")

    def run_live_submission_check(self) -> None:
        # Fireworks owns its endpoint semantics; a tiny request proves the exact
        # configured endpoint/model without lifecycle code guessing a models URL.
        self.run_full_readiness_probe()

    def execute_structured(
        self,
        task: TaskInput,
        *,
        schema: dict[str, Any],
        schema_name: str,
        schema_mode: str,
    ) -> TaskOutput:
        if schema_mode == "native_json_schema":
            raise ProviderCapabilityError("Native JSON Schema is not proven for this provider")
        return self.execute(task)

    def _validate_configuration(self) -> None:
        if self.configuration_status():
            return
        missing = [
            name
            for name, value in (
                ("FIREWORKS_API_KEY", self.api_key),
                ("FIREWORKS_BASE_URL", self.endpoint_url),
                ("FIREWORKS_MODEL", self.model),
            )
            if not value.strip()
        ]
        raise ProviderConfigurationError(
            "Selected provider configuration is incomplete",
            internal_detail="missing=" + ",".join(missing),
        )

    @staticmethod
    def _http_failure(status_code: int, attempt: int) -> ProviderError:
        detail = f"http_status={status_code};attempt={attempt}"
        if status_code in {401, 403}:
            return ProviderAuthenticationError(
                "Selected provider rejected authentication", internal_detail=detail
            )
        if status_code == 429:
            return ProviderRateLimitedError(
                "Selected provider rate limited the request", internal_detail=detail
            )
        if status_code >= 500:
            return ProviderUnavailableError(
                "Selected provider is unavailable", internal_detail=detail
            )
        return ProviderPermanentRequestError(
            "Selected provider rejected the request", internal_detail=detail
        )

    def execute(self, task: TaskInput) -> TaskOutput:
        self._validate_configuration()
        attempts = max(1, self.max_attempts)
        endpoint = self.endpoint_url.strip()
        payload = {
            "model": self.model,
            "prompt": task.prompt,
            "max_tokens": self.max_tokens,
            "top_k": 40,
            "temperature": 0.0,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        for attempt in range(1, attempts + 1):
            try:
                response = self.request(
                    endpoint,
                    headers=headers,
                    json=payload,
                    timeout=self.timeout_seconds,
                )
            except IsolatedHttpTimeout as exc:
                if attempt < attempts:
                    self.sleep(self.retry_delay_seconds * (2 ** (attempt - 1)))
                    continue
                raise ProviderTimeoutError(
                    "Selected provider request timed out",
                    internal_detail=(
                        f"attempt={attempt};child_pid={exc.child_pid};"
                        "client_process_terminated=true;server_inference_cancelled=unverified"
                    ),
                ) from None
            except requests.Timeout:
                if attempt < attempts:
                    self.sleep(self.retry_delay_seconds * (2 ** (attempt - 1)))
                    continue
                raise ProviderTimeoutError(
                    "Selected provider request timed out",
                    internal_detail=f"attempt={attempt}",
                ) from None
            except IsolatedHttpResponseTooLarge:
                raise ProviderResponseTooLargeError("Selected provider response exceeded its byte limit") from None
            except IsolatedHttpReapError:
                raise ProviderProcessReapError("Selected provider process could not be reaped") from None
            except IsolatedHttpBudgetError:
                raise StageBudgetExhaustedError("No provider budget remains after reap reserve") from None
            except (requests.RequestException, IsolatedHttpError) as exc:
                if attempt < attempts:
                    self.sleep(self.retry_delay_seconds * (2 ** (attempt - 1)))
                    continue
                raise ProviderUnavailableError(
                    "Selected provider is unavailable",
                    internal_detail=f"transport={type(exc).__name__};attempt={attempt}",
                ) from None

            if response.status_code != 200:
                retryable = (
                    response.status_code in self.transient_status_codes
                    or response.status_code == 429
                )
                if retryable and attempt < attempts:
                    self.sleep(self.retry_delay_seconds * (2 ** (attempt - 1)))
                    continue
                raise self._http_failure(response.status_code, attempt)

            try:
                data = response.json()
                choice = data["choices"][0]
                content = choice.get("text")
                if content is None:
                    content = choice["message"]["content"]
            except (KeyError, IndexError, TypeError, ValueError):
                raise ProviderInvalidResponseError(
                    "Selected provider returned an invalid response",
                    internal_detail="shape=invalid_completion",
                ) from None
            if content is None or isinstance(content, (dict, list)):
                raise ProviderInvalidResponseError(
                    "Selected provider returned an invalid response",
                    internal_detail=f"content_type={type(content).__name__}",
                )
            answer = content if isinstance(content, str) else str(content)
            if not answer.strip():
                raise ProviderInvalidResponseError(
                    "Selected provider returned an invalid response",
                    internal_detail="content=empty",
                )
            return TaskOutput(task_id=task.task_id, answer=answer)

        raise ProviderUnavailableError("Selected provider is unavailable")
