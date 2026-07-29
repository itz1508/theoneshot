"""Observe-first wiring of usage accounting into the provider lifecycle.

One provider invocation reserves exactly one usage attempt and always
finalizes it — success, malformed usage, and provider failure all reach a
terminal ledger phase.  Accounting never breaks the assistant path: every
entry point degrades to ``None`` evidence with a logged warning instead
of raising into the request flow.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..providers.base import AssistantProvider, CompletionReply, CompletionRequest
from ..usage import (
    ApproximateTokenEstimator,
    PreparedCompletionRequest,
    PreparedMessage,
    PricingRegistry,
    UsageAccountingEvidence,
    UsageAccountingService,
    UsageConfig,
    UsageLedger,
)
from ..usage.policies import AdmissionMode
from ..usage.service import UsageFinalization, UsagePreflight

logger = logging.getLogger("audisor_assistant")

#: Every provider invocation is a single accounted attempt.
ATTEMPT_ID = "1"


class UsageAttempt:
    """One reserved accounting attempt for one provider invocation."""

    def __init__(
        self,
        service: UsageAccountingService,
        prepared: PreparedCompletionRequest,
        preflight: UsagePreflight,
    ) -> None:
        self._service = service
        self._prepared = prepared
        self._preflight = preflight

    def _evidence(
        self, finalization: UsageFinalization | None
    ) -> UsageAccountingEvidence:
        return UsageAccountingEvidence.from_model_attempt(
            operation_id=self._prepared.operation_id,
            attempt_id=self._prepared.attempt_id,
            provider=self._prepared.provider,
            model=self._prepared.model,
            preflight=self._preflight,
            finalization=finalization,
        )

    def _finalize(
        self,
        *,
        native_usage: dict[str, object] | None,
        native_usage_invalid: bool,
        compat_usage: dict[str, int] | None,
    ) -> UsageAccountingEvidence:
        try:
            finalization = self._service.finalize_from_reply(
                self._prepared,
                self._preflight,
                native_usage=native_usage,
                native_usage_invalid=native_usage_invalid,
                compat_usage=compat_usage,
                at=datetime.now(timezone.utc),
            )
        except Exception:  # noqa: BLE001 - accounting must never break requests
            logger.warning("usage_accounting_finalize_failed", exc_info=True)
            finalization = None
        return self._evidence(finalization)

    def finalize_from_reply(self, reply: CompletionReply) -> UsageAccountingEvidence:
        return self._finalize(
            native_usage=reply.native_usage,
            native_usage_invalid=reply.native_usage_invalid,
            compat_usage=reply.usage,
        )

    def finalize_failure(self) -> UsageAccountingEvidence:
        """The provider call failed: close the attempt with no usage."""
        return self._finalize(
            native_usage=None, native_usage_invalid=False, compat_usage=None
        )


class UsageAccountingIntegration:
    """Environment-configured accounting service bound to the request flow."""

    def __init__(
        self, service: UsageAccountingService, config: UsageConfig
    ) -> None:
        self._service = service
        self._config = config

    @classmethod
    def from_environment(cls) -> "UsageAccountingIntegration | None":
        """Build from environment; any configuration failure degrades to
        no accounting (observe-first) with a logged warning."""
        try:
            config = UsageConfig.from_environment()
            if config.usage_mode is AdmissionMode.OFF:
                return None
            registry = (
                PricingRegistry.from_path(config.pricing_registry_path)
                if config.pricing_registry_path is not None
                else PricingRegistry(())
            )
            ledger = (
                UsageLedger(config.ledger_dir)
                if config.ledger_dir is not None
                else None
            )
            service = UsageAccountingService(
                estimator=ApproximateTokenEstimator(),
                pricing_registry=registry,
                ledger=ledger,
            )
            return cls(service, config)
        except Exception:  # noqa: BLE001 - accounting must never block startup
            logger.warning("usage_accounting_disabled_configuration", exc_info=True)
            return None

    def begin(
        self,
        provider: AssistantProvider,
        completion: CompletionRequest,
        request_id: str,
    ) -> UsageAttempt | None:
        """Reserve one attempt using the model frozen at the provider
        boundary — never an independently recomputed model id."""
        try:
            resolve = getattr(provider, "effective_model", None)
            if resolve is None:
                return None
            prepared = PreparedCompletionRequest(
                operation_id=request_id,
                attempt_id=ATTEMPT_ID,
                provider=provider.provider_id,
                model=resolve(completion.model_override),
                messages=(
                    PreparedMessage(role="system", content=completion.system_prompt),
                    *(
                        PreparedMessage(role=turn.role, content=turn.content)
                        for turn in completion.history
                    ),
                    PreparedMessage(role="user", content=completion.user_prompt),
                ),
                max_output_tokens=completion.max_tokens or 0,
            )
            preflight = self._service.preflight(
                prepared,
                at=datetime.now(timezone.utc),
                context_mode=self._config.context_mode,
                cost_mode=self._config.cost_mode,
                max_cost_usd=self._config.default_max_provider_charge_usd,
                allow_approximate=self._config.allow_approximate_counts,
            )
            return UsageAttempt(self._service, prepared, preflight)
        except Exception:  # noqa: BLE001 - accounting must never break requests
            logger.warning("usage_accounting_begin_failed", exc_info=True)
            return None


def begin_attempt(
    accounting: UsageAccountingIntegration | None,
    provider: AssistantProvider,
    completion: CompletionRequest,
    request_id: str,
) -> UsageAttempt | None:
    if accounting is None:
        return None
    return accounting.begin(provider, completion, request_id)


def finalize_reply(
    attempt: UsageAttempt | None, reply: CompletionReply
) -> UsageAccountingEvidence | None:
    return attempt.finalize_from_reply(reply) if attempt is not None else None


def finalize_failure(attempt: UsageAttempt | None) -> UsageAccountingEvidence | None:
    return attempt.finalize_failure() if attempt is not None else None


def not_applicable_evidence(
    accounting: UsageAccountingIntegration | None,
    *,
    operation_id: str,
    provider: str,
) -> UsageAccountingEvidence | None:
    """Non-LLM paths (e.g. LanguageTool) never consume provider tokens."""
    if accounting is None:
        return None
    return UsageAccountingEvidence.not_applicable(
        operation_id=operation_id, attempt_id=ATTEMPT_ID, provider=provider
    )
