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
    ProviderCapabilities,
    ProviderConfigurationError,
    ProviderInvalidResponseError,
    ProviderError,
    ProviderPermanentRequestError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


class ResponseLike(Protocol):
    status_code: int

    def json(self) -> Any:
        ...


RequestFunction = Callable[..., ResponseLike]
SleepFunction = Callable[[float], None]


@dataclass
class FireworksWorker:
    """Translate typed Audisor tasks to and from the Fireworks HTTP API."""

    api_key: str = field(repr=False)
    base_url: str
    model: str
    max_attempts: int = 2
    retry_delay_seconds: float = 0.25
    timeout_seconds: float = 300.0
    max_tokens: int = 4096
    request: RequestFunction = field(default=requests.post, repr=False)
    sleep: SleepFunction = field(default=time.sleep, repr=False)

    provider_id = "fireworks"
    transient_status_codes = frozenset({500, 502, 503, 504})

    @classmethod
    def from_environment(cls) -> "FireworksWorker":
        return cls(
            api_key=os.environ.get("FIREWORKS_API_KEY", ""),
            base_url=os.environ.get("FIREWORKS_BASE_URL", ""),
            model=os.environ.get("FIREWORKS_MODEL", ""),
        )

    def configuration_status(self) -> bool:
        return all(value.strip() for value in (self.api_key, self.base_url, self.model))

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(text=True)

    @staticmethod
    def normalize_base_url(base_url: str) -> str:
        normalized = base_url.rstrip("/")
        if normalized and not normalized.endswith("/v1"):
            normalized += "/v1"
        return normalized

    def _validate_configuration(self) -> None:
        if self.configuration_status():
            return
        missing = [
            name
            for name, value in (
                ("FIREWORKS_API_KEY", self.api_key),
                ("FIREWORKS_BASE_URL", self.base_url),
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
        endpoint = f"{self.normalize_base_url(self.base_url)}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": task.prompt}],
            "max_tokens": self.max_tokens,
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
            except requests.Timeout:
                if attempt < attempts:
                    self.sleep(self.retry_delay_seconds * (2 ** (attempt - 1)))
                    continue
                raise ProviderTimeoutError(
                    "Selected provider request timed out",
                    internal_detail=f"attempt={attempt}",
                ) from None
            except requests.RequestException as exc:
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
                content = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError, ValueError):
                raise ProviderInvalidResponseError(
                    "Selected provider returned an invalid response",
                    internal_detail="shape=invalid_chat_completion",
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
