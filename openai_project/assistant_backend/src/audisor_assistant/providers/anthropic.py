"""Native Anthropic cloud provider adapter.

Speaks the Anthropic Messages API directly (``x-api-key`` header,
``/v1/messages``).  All configuration is server-side environment only —
no credential is ever embedded, logged, or returned:

- AUDISOR_ASSISTANT_CLOUD_BASE_URL   (default https://api.anthropic.com)
- AUDISOR_ASSISTANT_CLOUD_MODEL_ID
- AUDISOR_ASSISTANT_CLOUD_API_KEY    (read at request time; never stored)
- AUDISOR_ASSISTANT_CLOUD_MODEL_CHOICES  (comma-separated dropdown list)
- AUDISOR_TIMEOUT_SECONDS / AUDISOR_MAX_TOKENS (shared)
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
from .cloud import cloud_model_choices
from .local_openai_compatible import DEFAULT_MAX_TOKENS, DEFAULT_TIMEOUT_SECONDS

DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"

#: Sanitized native usage whitelist — usage metadata only, nothing else
#: from the provider response ever crosses this boundary.
_NATIVE_USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)
#: Fields that must be present and well-formed for native usage to count.
_NATIVE_REQUIRED_FIELDS = ("input_tokens", "output_tokens")

#: Fixed, safe dropdown defaults; overridable via the choices env var.
DEFAULT_ANTHROPIC_MODEL_CHOICES = [
    "claude-sonnet-4-6",
    "claude-opus-4-8",
    "claude-haiku-4-5-20251001",
]


class CloudAnthropicProvider:
    provider_id = "cloud-anthropic"
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
        self.base_url = (
            base_url
            or os.environ.get(
                "AUDISOR_ASSISTANT_CLOUD_BASE_URL", DEFAULT_ANTHROPIC_BASE_URL
            )
        ).rstrip("/")
        self.model_id = model_id or os.environ.get(
            "AUDISOR_ASSISTANT_CLOUD_MODEL_ID", ""
        )
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
        if not self.model_id:
            raise ProviderError(
                PublicErrorCategory.CONFIGURATION,
                "Cloud provider model is not configured.",
            )

    def complete(self, request: CompletionRequest) -> CompletionReply:
        api_key = os.environ.get("AUDISOR_ASSISTANT_CLOUD_API_KEY", "")
        if not api_key:
            raise ProviderError(
                PublicErrorCategory.CONFIGURATION,
                "Cloud provider credential is not configured.",
            )
        body = {
            "model": self.effective_model(request.model_override),
            "max_tokens": request.max_tokens or self.max_tokens,
            "system": request.system_prompt,
            "messages": [
                *(
                    {"role": turn.role, "content": turn.content}
                    for turn in request.history
                ),
                {"role": "user", "content": request.user_prompt},
            ],
        }
        timeout = request.timeout_seconds or self.timeout_seconds
        try:
            response = requests.post(
                f"{self.base_url}/v1/messages",
                json=body,
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": ANTHROPIC_VERSION,
                    "content-type": "application/json",
                },
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
        return _parse_messages_response(
            response, model=self.effective_model(request.model_override)
        )

    def effective_model(self, model_override: str | None) -> str:
        return model_override or self.model_id

    def context_window(self, model_override: str | None) -> int | None:
        # No authoritative metadata endpoint is queried for cloud models;
        # unknown is reported explicitly rather than guessed.
        return None

    def list_models(self) -> ModelListing:
        return ModelListing(
            current_model=self.model_id,
            available_models=cloud_model_choices(
                list(DEFAULT_ANTHROPIC_MODEL_CHOICES)
            ),
            reachable=None,
        )


def _parse_messages_response(
    response: requests.Response, *, model: str | None = None
) -> CompletionReply:
    """Translate an Anthropic Messages response into a CompletionReply."""
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
        blocks = payload["content"]
        text = "".join(
            str(block.get("text", ""))
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise ProviderError(
            PublicErrorCategory.INVALID_RESPONSE,
            "Provider returned a malformed response.",
        ) from exc
    if not text.strip():
        raise ProviderError(
            PublicErrorCategory.INVALID_RESPONSE,
            "Provider returned an empty response.",
        )
    usage: dict[str, int] | None = None
    raw_usage = payload.get("usage")
    if isinstance(raw_usage, dict):
        prompt = raw_usage.get("input_tokens")
        completion = raw_usage.get("output_tokens")
        if isinstance(prompt, (int, float)) and isinstance(completion, (int, float)):
            usage = {
                "prompt_tokens": int(prompt),
                "completion_tokens": int(completion),
                "total_tokens": int(prompt) + int(completion),
            }
    native_usage, native_invalid = _sanitize_native_usage(raw_usage)
    return CompletionReply(
        text=text,
        usage=usage,
        native_usage=native_usage,
        native_usage_invalid=native_invalid,
        model=model,
    )


def _sanitize_native_usage(
    raw_usage: object,
) -> tuple[dict[str, object] | None, bool]:
    """Whitelist the native Anthropic usage counters.

    Absent usage → ``(None, False)``.  Present but malformed (not an
    object, or a required counter is not a non-negative int) →
    ``(None, True)`` so accounting can report ``native_usage_invalid``
    instead of silently falling back.  Unknown cache categories are
    simply omitted — never coerced to zero.
    """
    if raw_usage is None:
        return None, False
    if not isinstance(raw_usage, dict):
        return None, True
    native: dict[str, object] = {}
    for field in _NATIVE_USAGE_FIELDS:
        value = raw_usage.get(field)
        if value is None:
            continue
        if type(value) is not int or value < 0:
            return None, True
        native[field] = value
    if any(field not in native for field in _NATIVE_REQUIRED_FIELDS):
        return None, True
    return native, False
