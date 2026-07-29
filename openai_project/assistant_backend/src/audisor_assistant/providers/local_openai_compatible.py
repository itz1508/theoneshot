"""Local OpenAI-compatible provider.

Configured entirely from server-side environment variables (see
``openai_project/docs/local-model-provider.md``):

- AUDISOR_PROVIDER
- AUDISOR_BASE_URL
- AUDISOR_MODEL_ID
- AUDISOR_TIMEOUT_SECONDS
- AUDISOR_MAX_TOKENS

Local models are best-effort; failures are normalized into public error
categories and never fall back silently to another provider.
"""
from __future__ import annotations

import os

import requests

from ..schemas.responses import PublicErrorCategory
from .base import (
    CompletionReply,
    CompletionRequest,
    ModelListing,
    ProviderCapabilities,
    ProviderError,
)

DEFAULT_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_TIMEOUT_SECONDS = 300.0
DEFAULT_MAX_TOKENS = 1024
MODEL_LIST_TIMEOUT_SECONDS = 5.0


class LocalOpenAICompatibleProvider:
    provider_id = "local-openai-compatible"
    source = "local"
    capabilities = ProviderCapabilities()

    def __init__(
        self,
        *,
        base_url: str | None = None,
        model_id: str | None = None,
        timeout_seconds: float | None = None,
        max_tokens: int | None = None,
    ) -> None:
        self.base_url = (base_url or os.environ.get("AUDISOR_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")
        self.model_id = model_id or os.environ.get("AUDISOR_MODEL_ID", "")
        try:
            self.timeout_seconds = timeout_seconds if timeout_seconds is not None else float(
                os.environ.get("AUDISOR_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))
            )
            self.max_tokens = max_tokens if max_tokens is not None else int(
                os.environ.get("AUDISOR_MAX_TOKENS", str(DEFAULT_MAX_TOKENS))
            )
        except (ValueError, TypeError) as exc:
            raise ProviderError(
                PublicErrorCategory.CONFIGURATION,
                "Local provider numeric configuration is invalid.",
            ) from exc
        if not self.model_id:
            raise ProviderError(
                PublicErrorCategory.CONFIGURATION,
                "Local provider model is not configured.",
            )

    def complete(self, request: CompletionRequest) -> CompletionReply:
        body = {
            "model": request.model_override or self.model_id,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
            "max_tokens": request.max_tokens or self.max_tokens,
            "temperature": 0,
            "stream": False,
        }
        timeout = request.timeout_seconds or self.timeout_seconds
        try:
            response = requests.post(
                f"{self.base_url}/v1/chat/completions",
                json=body,
                timeout=timeout,
            )
        except requests.Timeout as exc:
            raise ProviderError(
                PublicErrorCategory.TIMEOUT, "Local provider timed out."
            ) from exc
        except requests.ConnectionError as exc:
            raise ProviderError(
                PublicErrorCategory.UNAVAILABLE, "Local provider is unavailable."
            ) from exc
        except requests.RequestException as exc:
            raise ProviderError(
                PublicErrorCategory.INTERNAL, "Local provider request failed."
            ) from exc
        return _parse_chat_completion(response)

    def list_models(self) -> ModelListing:
        """Probe the local engine's tag list; unreachable is a listing
        state, not an error — the configured model stays offered."""
        try:
            response = requests.get(
                f"{self.base_url}/api/tags", timeout=MODEL_LIST_TIMEOUT_SECONDS
            )
            response.raise_for_status()
            names = [
                str(item["name"])
                for item in response.json().get("models", [])
                if isinstance(item, dict) and "name" in item
            ]
        except (requests.RequestException, ValueError):
            names = []
        if not names:
            return ModelListing(
                current_model=self.model_id,
                available_models=[self.model_id],
                reachable=False,
            )
        return ModelListing(
            current_model=self.model_id, available_models=names, reachable=True
        )


def _parse_chat_completion(response: requests.Response) -> CompletionReply:
    """Translate an OpenAI-compatible chat response into a CompletionReply."""
    if response.status_code in (401, 403):
        raise ProviderError(
            PublicErrorCategory.AUTHENTICATION, "Provider rejected authentication."
        )
    if response.status_code == 429:
        raise ProviderError(
            PublicErrorCategory.RATE_LIMITED, "Provider rate limit reached."
        )
    if response.status_code >= 500:
        raise ProviderError(
            PublicErrorCategory.UNAVAILABLE, "Provider returned a server error."
        )
    if response.status_code != 200:
        raise ProviderError(
            PublicErrorCategory.INVALID_RESPONSE,
            "Provider returned an unexpected status.",
        )
    try:
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ProviderError(
            PublicErrorCategory.INVALID_RESPONSE,
            "Provider returned a malformed response.",
        ) from exc
    if not isinstance(content, str) or not content.strip():
        raise ProviderError(
            PublicErrorCategory.INVALID_RESPONSE,
            "Provider returned an empty response.",
        )
    usage: dict[str, int] | None = None
    raw_usage = payload.get("usage")
    if isinstance(raw_usage, dict):
        usage = {
            key: int(value)
            for key, value in raw_usage.items()
            if key in ("prompt_tokens", "completion_tokens", "total_tokens")
            and isinstance(value, (int, float))
        }
    return CompletionReply(text=content, usage=usage or None)
