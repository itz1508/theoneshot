"""Local model failure policy for Audisor.

When the local model (Ollama or other local-openai-compatible provider)
fails, Audisor must NOT silently fall back to a remote/cloud provider.
This is a security and predictability boundary: users explicitly choose
local execution for data sovereignty, cost control, or compliance.

Failure modes and responses:
1. Provider timeout → retry up to max_retries (from config), then fail
2. Provider connection error → retry with backoff, then fail
3. Model returns malformed JSON → retry once with structured_output=True,
   then fail with candidate_schema_failed
4. Model returns forbidden fields → fail immediately, no retry
5. Model returns tool calls → fail immediately, no retry
6. Model returns multiple choices → fail immediately, no retry
7. Model returns empty answer → fail immediately, no retry
8. Model returns non-JSON → retry once with explicit JSON prompt,
   then fail

All failures surface through the canonical error contract with:
- category: "provider"
- stage: "model_invocation"
- retryable: True (for transient) or False (for permanent)
- partial_result_safe: False (never safe to proceed with partial analysis)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal

from audisor.schemas.errors import AudisorErrorCode, AudisorErrorDetail, AudisorError


@dataclass(frozen=True)
class ModelFailurePolicy:
    """Immutable policy for handling local model failures."""

    max_retries: int = 3
    retry_backoff_seconds: float = 2.0
    allow_structured_output_retry: bool = True
    allow_json_prompt_retry: bool = True
    allow_silent_remote_fallback: bool = False  # NEVER True

    def should_retry(
        self,
        failure_code: str,
        attempt: int,
    ) -> tuple[bool, float]:
        """Determine if a retry is permitted and the backoff delay.

        Returns:
            (should_retry, delay_seconds)
        """
        if self.allow_silent_remote_fallback:
            # This should never happen, but if it does, block it
            return False, 0.0

        if attempt >= self.max_retries:
            return False, 0.0

        # Permanent failures: never retry
        permanent_codes = {
            "tool_call_not_allowed",
            "invalid_response_framing",
            "host_owned_field_in_candidate",
            "forbidden_legacy_fields",
            "candidate_schema_failed",
            "empty_response",
        }
        if failure_code in permanent_codes:
            return False, 0.0

        # Transient failures: retry with backoff
        transient_codes = {
            "provider_timeout",
            "provider_failed",
            "content_hash_mismatch",
            "package_persistence_failed",
            "package_hash_mismatch",
        }
        if failure_code in transient_codes:
            delay = self.retry_backoff_seconds * (2 ** attempt)
            return True, delay

        # Unknown codes: conservative, no retry
        return False, 0.0

    def create_error(
        self,
        failure_code: str,
        message: str,
        detail: str = "",
    ) -> AudisorError:
        """Create a canonical error for a model failure."""
        retryable = self.should_retry(failure_code, 0)[0]

        return AudisorError(
            error_code=AudisorErrorCode(
                category="provider",
                stage="model_invocation",
                code=failure_code,
                retryable=retryable,
                max_retries=self.max_retries if retryable else 0,
                partial_result_safe=False,
            ),
            error_detail=AudisorErrorDetail(
                message=message,
                detail=detail,
                suggested_action=(
                    "Check local model availability with 'audisor setup'"
                    if failure_code in ("provider_timeout", "provider_failed")
                    else "Review the task and plan for schema compliance"
                ),
            ),
            timestamp="",  # Will be set by AudisorError model validator
        )


# Global default policy - no silent remote fallback
DEFAULT_MODEL_FAILURE_POLICY = ModelFailurePolicy()