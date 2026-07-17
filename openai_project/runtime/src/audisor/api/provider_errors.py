"""Translate normalized provider failures into stable public HTTP errors."""

from fastapi import HTTPException

from audisor.workers.base import (
    ProviderConfigurationError,
    ProviderError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


def provider_http_exception(error: ProviderError) -> HTTPException:
    unavailable = (
        ProviderConfigurationError,
        ProviderUnavailableError,
        ProviderRateLimitedError,
        ProviderTimeoutError,
    )
    status_code = 503 if isinstance(error, unavailable) else 502
    return HTTPException(
        status_code=status_code,
        detail={"code": error.code, "message": str(error)},
    )
