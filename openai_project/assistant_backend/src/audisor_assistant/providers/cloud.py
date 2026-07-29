"""Cloud provider adapter.

Vendor-neutral: talks to any cloud endpoint that accepts the same
OpenAI-compatible chat shape.  All configuration is server-side
environment only — no vendor is hardcoded and no credential is ever
embedded, logged, or returned:

- AUDISOR_ASSISTANT_CLOUD_BASE_URL
- AUDISOR_ASSISTANT_CLOUD_MODEL_ID
- AUDISOR_ASSISTANT_CLOUD_API_KEY  (read at request time; never stored)
- AUDISOR_ASSISTANT_CLOUD_MODEL_CHOICES  (comma-separated dropdown list)
- AUDISOR_TIMEOUT_SECONDS / AUDISOR_MAX_TOKENS (shared)

The cloud vendor selection itself remains an open production decision.
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
from .local_openai_compatible import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_TIMEOUT_SECONDS,
    _parse_chat_completion,
)

CLOUD_MODEL_CHOICES_VAR = "AUDISOR_ASSISTANT_CLOUD_MODEL_CHOICES"


def cloud_model_choices(default: list[str]) -> list[str]:
    """Fixed, server-configured dropdown choices — cloud providers are
    never probed for listing, so no credential is ever used here."""
    raw = os.environ.get(CLOUD_MODEL_CHOICES_VAR, "")
    choices = [entry.strip() for entry in raw.split(",") if entry.strip()]
    return choices or default


class CloudOpenAICompatibleProvider:
    provider_id = "cloud-openai-compatible"
    source = "cloud"
    capabilities = ProviderCapabilities()

    def __init__(
        self,
        *,
        base_url: str | None = None,
        model_id: str | None = None,
        timeout_seconds: float | None = None,
        max_tokens: int | None = None,
    ) -> None:
        self.base_url = (base_url or os.environ.get("AUDISOR_ASSISTANT_CLOUD_BASE_URL", "")).rstrip("/")
        self.model_id = model_id or os.environ.get("AUDISOR_ASSISTANT_CLOUD_MODEL_ID", "")
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
                "Cloud provider numeric configuration is invalid.",
            ) from exc
        if not self.base_url or not self.model_id:
            raise ProviderError(
                PublicErrorCategory.CONFIGURATION,
                "Cloud provider endpoint is not configured.",
            )

    def complete(self, request: CompletionRequest) -> CompletionReply:
        api_key = os.environ.get("AUDISOR_ASSISTANT_CLOUD_API_KEY", "")
        if not api_key:
            raise ProviderError(
                PublicErrorCategory.CONFIGURATION,
                "Cloud provider credential is not configured.",
            )
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
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=timeout,
            )
        except requests.Timeout as exc:
            raise ProviderError(
                PublicErrorCategory.TIMEOUT, "Cloud provider timed out."
            ) from exc
        except requests.ConnectionError as exc:
            raise ProviderError(
                PublicErrorCategory.UNAVAILABLE, "Cloud provider is unavailable."
            ) from exc
        except requests.RequestException as exc:
            raise ProviderError(
                PublicErrorCategory.INTERNAL, "Cloud provider request failed."
            ) from exc
        return _parse_chat_completion(response)

    def list_models(self) -> ModelListing:
        return ModelListing(
            current_model=self.model_id,
            available_models=cloud_model_choices([self.model_id]),
            reachable=None,
        )
