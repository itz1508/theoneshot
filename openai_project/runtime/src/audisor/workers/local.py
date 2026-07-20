"""Local OpenAI-compatible provider adapter; endpoint details stay here."""

from __future__ import annotations

import os
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


@dataclass
class LocalWorker:
    """Translate typed Audisor tasks to a configured compatible endpoint."""

    base_url: str
    model_id: str
    api_key: str = field(default="", repr=False)
    timeout_seconds: float = 300.0
    max_tokens: int = 4096
    structured_output: bool = False
    request: RequestFunction = field(default=requests.post, repr=False)

    provider_id = "local-openai-compatible"

    @classmethod
    def from_environment(cls) -> "LocalWorker":
        return cls(
            base_url=os.environ.get("LOCAL_MODEL_BASE_URL", ""),
            model_id=os.environ.get("LOCAL_MODEL_ID", ""),
            api_key=os.environ.get("LOCAL_MODEL_API_KEY", ""),
        )

    def configuration_status(self) -> bool:
        return all(value.strip() for value in (self.base_url, self.model_id))

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
                ("LOCAL_MODEL_BASE_URL", self.base_url),
                ("LOCAL_MODEL_ID", self.model_id),
            )
            if not value.strip()
        ]
        raise ProviderConfigurationError(
            "Selected provider configuration is incomplete",
            internal_detail="missing=" + ",".join(missing),
        )

    @staticmethod
    def _http_failure(status_code: int) -> ProviderError:
        detail = f"http_status={status_code}"
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
        endpoint = f"{self.normalize_base_url(self.base_url)}/chat/completions"
        payload = {
            "model": self.model_id,
            "messages": [{"role": "user", "content": task.prompt}],
            "max_tokens": self.max_tokens,
            "temperature": 0.0,
        }
        if self.structured_output:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Content-Type": "application/json"}
        if self.api_key.strip():
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            response = self.request(
                endpoint,
                headers=headers,
                json=payload,
                timeout=self.timeout_seconds,
            )
        except requests.Timeout:
            raise ProviderTimeoutError(
                "Selected provider request timed out", internal_detail="attempt=1"
            ) from None
        except requests.RequestException as exc:
            raise ProviderUnavailableError(
                "Selected provider is unavailable",
                internal_detail=f"transport={type(exc).__name__};attempt=1",
            ) from None
        if response.status_code != 200:
            raise self._http_failure(response.status_code)
        try:
            data = response.json()
            choices = data["choices"]
            choice = choices[0]
            message = choice["message"]
            content = message["content"]
            finish_reason = choice.get("finish_reason")
            tool_call_present = bool(message.get("tool_calls"))
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
        return TaskOutput(task_id=task.task_id, answer=answer).set_response_metadata(
            http_status=response.status_code,
            transport_succeeded=True,
            finish_reason=finish_reason if isinstance(finish_reason, str) else None,
            tool_call_present=tool_call_present,
            choice_count=len(choices) if isinstance(choices, list) else None,
        )
