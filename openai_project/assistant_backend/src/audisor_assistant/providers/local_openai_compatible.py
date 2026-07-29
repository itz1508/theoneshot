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
        # Per-model context-window cache: only successful lookups are
        # cached so a temporarily unreachable engine is retried later.
        self._context_windows: dict[str, int] = {}

    def complete(self, request: CompletionRequest) -> CompletionReply:
        body = {
            "model": self.effective_model(request.model_override),
            "messages": [
                {"role": "system", "content": request.system_prompt},
                *(
                    {"role": turn.role, "content": turn.content}
                    for turn in request.history
                ),
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
        return _parse_chat_completion(
            response, model=self.effective_model(request.model_override)
        )

    def effective_model(self, model_override: str | None) -> str:
        return model_override or self.model_id

    def context_window(self, model_override: str | None) -> int | None:
        """Authoritative context window from the engine's model metadata
        (Ollama ``/api/show`` → ``model_info.<arch>.context_length``).
        Unknown or unreachable is reported as ``None`` — never guessed."""
        model = self.effective_model(model_override)
        if model in self._context_windows:
            return self._context_windows[model]
        try:
            response = requests.post(
                f"{self.base_url}/api/show",
                json={"model": model},
                timeout=MODEL_LIST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            model_info = response.json().get("model_info")
        except (requests.RequestException, ValueError):
            return None
        if not isinstance(model_info, dict):
            return None
        for key, value in model_info.items():
            if key.endswith(".context_length") and isinstance(value, int) and value > 0:
                self._context_windows[model] = value
                return value
        return None

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


def _parse_chat_completion(
    response: requests.Response, *, model: str | None = None
) -> CompletionReply:
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
    native_usage, native_invalid = _sanitize_native_usage(raw_usage)
    return CompletionReply(
        text=content,
        usage=usage or None,
        native_usage=native_usage,
        native_usage_invalid=native_invalid,
        model=model,
    )


#: Sanitized OpenAI-native usage whitelist — usage metadata only.
_NATIVE_COUNTERS = ("prompt_tokens", "completion_tokens", "total_tokens")
_NATIVE_DETAILS = {
    "prompt_tokens_details": ("cached_tokens",),
    "completion_tokens_details": ("reasoning_tokens",),
}
_NATIVE_REQUIRED = ("prompt_tokens", "completion_tokens")


def _sanitize_native_usage(
    raw_usage: object,
) -> tuple[dict[str, object] | None, bool]:
    """Whitelist native OpenAI-style usage counters and detail objects.

    Absent usage → ``(None, False)``.  Present but malformed →
    ``(None, True)`` — accounting reports ``native_usage_invalid``
    instead of silently falling back.  Unknown detail categories are
    omitted, never coerced to zero.
    """
    if raw_usage is None:
        return None, False
    if not isinstance(raw_usage, dict):
        return None, True
    native: dict[str, object] = {}
    for field in _NATIVE_COUNTERS:
        value = raw_usage.get(field)
        if value is None:
            continue
        if type(value) is not int or value < 0:
            return None, True
        native[field] = value
    if any(field not in native for field in _NATIVE_REQUIRED):
        return None, True
    for details_key, detail_fields in _NATIVE_DETAILS.items():
        details = raw_usage.get(details_key)
        if details is None:
            continue
        if not isinstance(details, dict):
            return None, True
        sanitized: dict[str, object] = {}
        for field in detail_fields:
            value = details.get(field)
            if value is None:
                continue
            if type(value) is not int or value < 0:
                return None, True
            sanitized[field] = value
        if sanitized:
            native[details_key] = sanitized
    return native, False
