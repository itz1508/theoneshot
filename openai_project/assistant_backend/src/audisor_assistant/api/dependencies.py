"""FastAPI dependencies: server-side provider selection and authentication.

Provider selection is controlled only by the ``AUDISOR_PROVIDER``
environment variable — never by browser input.  There is no fallback
chain: the configured provider either serves the request or the request
fails with a normalized error.
"""
from __future__ import annotations

import os

from fastapi import Depends, HTTPException, Request

from ..application.fix_engines import (
    FixEngineSelector,
    GrammarEngineState,
    ModelFixEngine,
)
from ..application.service import AssistantService
from ..auth.development import DevelopmentAuthProvider
from ..auth.ports import AuthContext, AuthenticationError, AuthProvider
from ..providers.base import (
    AssistantProvider,
    CompletionReply,
    CompletionRequest,
    DeterministicFakeProvider,
    ProviderCapabilities,
    ProviderError,
)
from ..providers.cloud import CloudOpenAICompatibleProvider
from ..providers.local_openai_compatible import LocalOpenAICompatibleProvider

PROVIDER_VAR = "AUDISOR_PROVIDER"

_PROVIDER_FACTORIES = {
    "fake-deterministic": DeterministicFakeProvider,
    "local-openai-compatible": LocalOpenAICompatibleProvider,
    "cloud-openai-compatible": CloudOpenAICompatibleProvider,
}


def build_provider(provider_id: str | None = None) -> AssistantProvider:
    """Instantiate the configured provider.  Raises ProviderError with a
    configuration category when the selection is unknown or incomplete."""
    selected = (provider_id or os.environ.get(PROVIDER_VAR, "local-openai-compatible")).strip()
    factory = _PROVIDER_FACTORIES.get(selected)
    if factory is None:
        from ..schemas.responses import PublicErrorCategory

        raise ProviderError(
            PublicErrorCategory.CONFIGURATION,
            "Configured provider is not supported.",
        )
    return factory()


def get_auth_provider() -> AuthProvider:
    return DevelopmentAuthProvider()


def get_auth_context(
    request: Request, auth: AuthProvider = Depends(get_auth_provider)
) -> AuthContext:
    try:
        return auth.authenticate(dict(request.headers))
    except AuthenticationError as error:
        raise HTTPException(status_code=401, detail=str(error)) from error


def get_service(request: Request) -> AssistantService:
    override = getattr(request.app.state, "service", None)
    if override is not None:
        return override
    try:
        provider: AssistantProvider = build_provider()
    except ProviderError as error:
        # Surface the configuration failure as a normalized envelope
        # instead of an internal server error.
        provider = _MisconfiguredProvider(error)
    grammar_state = getattr(request.app.state, "grammar_state", None)
    selector = FixEngineSelector.from_env(
        ModelFixEngine(provider), grammar_state or GrammarEngineState()
    )
    return AssistantService(provider, fix_selector=selector)


class _MisconfiguredProvider:
    """Placeholder provider that re-raises its configuration error so the
    service produces a normalized failed envelope."""

    provider_id = "unconfigured"
    source = "local"
    capabilities = ProviderCapabilities()

    def __init__(self, error: ProviderError) -> None:
        self._error = error

    def complete(self, request: CompletionRequest) -> CompletionReply:
        raise self._error
