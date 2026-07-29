"""Integration tests: usage accounting wired into the request lifecycle.

One provider invocation reserves and finalizes exactly one usage
attempt; normalized evidence is attached to the public assistant
response; accounting stays opt-in and never breaks the request path.
"""
from __future__ import annotations

import json
from pathlib import Path

from audisor_assistant.application.fix_engines import (
    FixEngineSelector,
    GrammarEngineState,
    GrammarMatch,
    LanguageToolFixEngine,
    ModelFixEngine,
)
from audisor_assistant.application.service import AssistantService
from audisor_assistant.application.usage_integration import (
    ATTEMPT_ID,
    UsageAccountingIntegration,
)
from audisor_assistant.providers.base import (
    CompletionReply,
    CompletionRequest,
    DeterministicFakeProvider,
    ProviderCapabilities,
    ProviderError,
)
from audisor_assistant.schemas.requests import AssistantRequest
from audisor_assistant.schemas.responses import AssistantStatus, PublicErrorCategory
from audisor_assistant.usage import (
    ApproximateTokenEstimator,
    PricingRegistry,
    UsageAccountingService,
    UsageConfig,
    UsageLedger,
)
from audisor_assistant.usage.policies import AdmissionMode


def _integration(tmp_path: Path) -> tuple[UsageAccountingIntegration, UsageLedger]:
    ledger = UsageLedger(tmp_path / "ledger")
    service = UsageAccountingService(
        estimator=ApproximateTokenEstimator(),
        pricing_registry=PricingRegistry(()),
        ledger=ledger,
    )
    config = UsageConfig(
        usage_mode=AdmissionMode.OBSERVE,
        context_mode=AdmissionMode.OBSERVE,
        cost_mode=AdmissionMode.OFF,
        allow_remote_token_count=False,
        allow_approximate_counts=True,
        require_reviewed_pricing=False,
        default_max_provider_charge_usd=None,
        ledger_dir=tmp_path / "ledger",
        pricing_registry_path=None,
    )
    return UsageAccountingIntegration(service, config), ledger


def _request(mode: str = "expand_idea", **overrides) -> AssistantRequest:
    payload = {"request_id": "req-usage-1", "mode": mode, "text": "hello there"}
    payload.update(overrides)
    return AssistantRequest.model_validate(payload)


class _StubProvider:
    """Records calls; replies with a fixed CompletionReply or raises."""

    provider_id = "stub"
    source = "local"
    capabilities = ProviderCapabilities()

    def __init__(
        self,
        reply: CompletionReply | None = None,
        error: ProviderError | None = None,
    ) -> None:
        self.calls: list[CompletionRequest] = []
        self._reply = reply
        self._error = error

    def complete(self, request: CompletionRequest) -> CompletionReply:
        self.calls.append(request)
        if self._error is not None:
            raise self._error
        assert self._reply is not None
        return self._reply

    def effective_model(self, model_override: str | None) -> str:
        return model_override or "stub-model"


def test_success_attaches_finalized_evidence(tmp_path: Path) -> None:
    accounting, ledger = _integration(tmp_path)
    service = AssistantService(DeterministicFakeProvider(), accounting=accounting)

    response = service.handle(_request())

    assert response.status is AssistantStatus.COMPLETED
    evidence = response.accounting
    assert evidence is not None
    assert evidence.operation_id == "req-usage-1"
    assert evidence.attempt_id == ATTEMPT_ID
    assert evidence.provider == "fake-deterministic"
    assert evidence.model == "fake-deterministic"
    assert evidence.accounting_status.value == "complete"
    # Estimates and provider-reported actuals stay separate.
    assert evidence.estimate is not None
    assert evidence.actual is not None
    assert evidence.actual.input_tokens > 0  # content-derived deterministic count
    # Empty registry: no cost, labelled — never a fake zero charge.
    assert evidence.actual_cost is None
    assert "pricing_unavailable" in evidence.warnings
    record = ledger.get("req-usage-1", ATTEMPT_ID)
    assert record is not None and record.phase == "finalized"


def test_evidence_model_is_the_frozen_effective_model(tmp_path: Path) -> None:
    accounting, _ledger = _integration(tmp_path)
    service = AssistantService(DeterministicFakeProvider(), accounting=accounting)

    response = service.handle(_request(model="override-model"))

    assert response.accounting is not None
    assert response.accounting.model == "override-model"


def test_provider_failure_still_finalizes_the_attempt(tmp_path: Path) -> None:
    accounting, ledger = _integration(tmp_path)
    provider = _StubProvider(
        error=ProviderError(PublicErrorCategory.UNAVAILABLE, "Provider down.")
    )
    service = AssistantService(provider, accounting=accounting)

    response = service.handle(_request())

    assert response.status is AssistantStatus.FAILED
    evidence = response.accounting
    assert evidence is not None
    assert evidence.accounting_status.value == "incomplete"
    assert "provider_usage_missing" in evidence.warnings
    record = ledger.get("req-usage-1", ATTEMPT_ID)
    assert record is not None and record.phase == "finalized"


def test_invalid_native_usage_is_explicit_not_silent(tmp_path: Path) -> None:
    accounting, _ledger = _integration(tmp_path)
    reply = CompletionReply(
        text=json.dumps(
            {"expanded_text": "expanded", "preserved_intent": "kept"}
        ),
        usage={"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
        native_usage=None,
        native_usage_invalid=True,
        model="stub-model",
    )
    service = AssistantService(_StubProvider(reply=reply), accounting=accounting)

    response = service.handle(_request())

    assert response.status is AssistantStatus.COMPLETED
    evidence = response.accounting
    assert evidence is not None
    assert "native_usage_invalid" in evidence.warnings
    # No silent fallback to the compatibility values.
    assert evidence.accounting_status.value == "incomplete"
    assert evidence.actual is not None
    assert evidence.actual.input_tokens is None


def test_accounting_stays_opt_in(tmp_path: Path) -> None:
    service = AssistantService(DeterministicFakeProvider())
    response = service.handle(_request())
    assert response.accounting is None


def test_from_environment_off_mode_disables_accounting(monkeypatch) -> None:
    monkeypatch.setenv("AUDISOR_USAGE_MODE", "off")
    assert UsageAccountingIntegration.from_environment() is None


def test_from_environment_defaults_returns_observe_integration(monkeypatch) -> None:
    """With no AUDISOR_* env vars set, accounting is observe-only —
    never disabled, never enforcing."""
    for var in [
        "AUDISOR_USAGE_MODE",
        "AUDISOR_CONTEXT_MODE",
        "AUDISOR_COST_MODE",
        "AUDISOR_ALLOW_REMOTE_TOKEN_COUNT",
        "AUDISOR_ALLOW_APPROXIMATE_COUNTS",
        "AUDISOR_REQUIRE_REVIEWED_PRICING",
        "AUDISOR_DEFAULT_MAX_PROVIDER_CHARGE_USD",
        "AUDISOR_USAGE_LEDGER_DIR",
        "AUDISOR_PRICING_REGISTRY_PATH",
    ]:
        monkeypatch.delenv(var, raising=False)
    integration = UsageAccountingIntegration.from_environment()
    assert integration is not None


def test_from_environment_invalid_configuration_disables_accounting(
    monkeypatch,
) -> None:
    monkeypatch.setenv("AUDISOR_USAGE_MODE", "not-a-real-mode")
    assert UsageAccountingIntegration.from_environment() is None


def test_failure_path_ledger_reaches_terminal_and_error_semantics_preserved(
    tmp_path: Path,
) -> None:
    """When the provider raises, the reserved attempt reaches a terminal
    ledger phase, no reservation remains open, and the original
    ProviderError semantics are preserved in the response."""
    accounting, ledger = _integration(tmp_path)
    provider = _StubProvider(
        error=ProviderError(PublicErrorCategory.TIMEOUT, "Provider down.")
    )
    service = AssistantService(provider, accounting=accounting)

    response = service.handle(_request())

    assert response.status is AssistantStatus.FAILED
    assert response.result["error"]["category"] == "timeout"
    record = ledger.get("req-usage-1", ATTEMPT_ID)
    assert record is not None
    assert record.phase in {"finalized", "blocked"}


class _BrokenEstimator:
    def estimate(self, request):
        raise RuntimeError("estimator exploded")


class _BrokenLedger(UsageLedger):
    def reserve(self, record):  # type: ignore[override]
        raise RuntimeError("ledger exploded")

    def finalize(self, **_kwargs):  # type: ignore[override]
        raise RuntimeError("ledger exploded")


def test_accounting_internal_failure_does_not_replace_assistant_result(
    tmp_path: Path,
) -> None:
    """Internal accounting failures (estimator, ledger, ...) are contained:
    they never replace the normal assistant result or the existing
    provider/application error."""
    broken_service = UsageAccountingService(
        estimator=_BrokenEstimator(),
        pricing_registry=PricingRegistry(()),
    )
    config = UsageConfig(
        usage_mode=AdmissionMode.OBSERVE,
        context_mode=AdmissionMode.OBSERVE,
        cost_mode=AdmissionMode.OFF,
        allow_remote_token_count=False,
        allow_approximate_counts=True,
        require_reviewed_pricing=False,
        default_max_provider_charge_usd=None,
        ledger_dir=None,
        pricing_registry_path=None,
    )
    accounting = UsageAccountingIntegration(broken_service, config)
    service = AssistantService(DeterministicFakeProvider(), accounting=accounting)

    response = service.handle(_request())

    # The assistant result is complete and correct — accounting's
    # internal failure did not replace it.
    assert response.status is AssistantStatus.COMPLETED
    assert response.result


def test_accounting_survives_broken_ledger(tmp_path: Path) -> None:
    """Ledger failures during reserve/finalize must not break the request."""
    broken_ledger = _BrokenLedger(tmp_path / "broken")
    service = UsageAccountingService(
        estimator=ApproximateTokenEstimator(),
        pricing_registry=PricingRegistry(()),
        ledger=broken_ledger,
    )
    config = UsageConfig(
        usage_mode=AdmissionMode.OBSERVE,
        context_mode=AdmissionMode.OBSERVE,
        cost_mode=AdmissionMode.OFF,
        allow_remote_token_count=False,
        allow_approximate_counts=True,
        require_reviewed_pricing=False,
        default_max_provider_charge_usd=None,
        ledger_dir=tmp_path / "broken",
        pricing_registry_path=None,
    )
    accounting = UsageAccountingIntegration(service, config)
    service = AssistantService(DeterministicFakeProvider(), accounting=accounting)

    response = service.handle(_request())

    assert response.status is AssistantStatus.COMPLETED


# --- fix-wording provider-failure accounting ----------------------------


def test_fix_wording_provider_failure_preserves_accounting_evidence(
    tmp_path: Path, monkeypatch,
) -> None:
    """When the provider raises during fix_wording, the reserved attempt
    reaches a terminal ledger phase and the failure evidence is attached
    to the response envelope — not silently dropped."""
    for var in ("AUDISOR_FIX_ENGINE", "AUDISOR_FIX_FALLBACK"):
        monkeypatch.delenv(var, raising=False)
    accounting, ledger = _integration(tmp_path)
    provider = _StubProvider(
        error=ProviderError(PublicErrorCategory.UNAVAILABLE, "Provider down.")
    )
    selector = FixEngineSelector.from_env(ModelFixEngine(provider))
    service = AssistantService(
        provider, accounting=accounting, fix_selector=selector,
    )

    response = service.handle(_request("fix_wording"))

    assert response.status is AssistantStatus.FAILED
    assert response.result["error"]["category"] == "unavailable"
    # Accounting evidence is present and reflects the failure.
    evidence = response.accounting
    assert evidence is not None
    assert evidence.operation_id == "req-usage-1"
    assert evidence.attempt_id == ATTEMPT_ID
    assert evidence.accounting_status.value == "incomplete"
    assert "provider_usage_missing" in evidence.warnings
    # The ledger record reached a terminal phase.
    record = ledger.get("req-usage-1", ATTEMPT_ID)
    assert record is not None
    assert record.phase in {"finalized", "blocked"}


class _FinalizeBrokenLedger(UsageLedger):
    """Ledger that succeeds on reserve() but raises on finalize()."""

    def finalize(self, **_kwargs):  # type: ignore[override]
        raise RuntimeError("ledger finalization exploded")


def test_fix_wording_reservation_succeeds_but_finalization_raises(
    tmp_path: Path, monkeypatch,
) -> None:
    """When the ledger reservation succeeds but finalization raises,
    the assistant response remains valid and the error is contained
    within the accounting path."""
    for var in ("AUDISOR_FIX_ENGINE", "AUDISOR_FIX_FALLBACK"):
        monkeypatch.delenv(var, raising=False)
    broken_ledger = _FinalizeBrokenLedger(tmp_path / "broken-ledger")
    broken_service = UsageAccountingService(
        estimator=ApproximateTokenEstimator(),
        pricing_registry=PricingRegistry(()),
        ledger=broken_ledger,
    )
    config = UsageConfig(
        usage_mode=AdmissionMode.OBSERVE,
        context_mode=AdmissionMode.OBSERVE,
        cost_mode=AdmissionMode.OFF,
        allow_approximate_counts=True,
        allow_remote_token_count=False,
        require_reviewed_pricing=False,
        default_max_provider_charge_usd=None,
        ledger_dir=tmp_path / "broken-ledger",
        pricing_registry_path=None,
    )
    accounting = UsageAccountingIntegration(broken_service, config)
    provider = DeterministicFakeProvider()
    selector = FixEngineSelector.from_env(ModelFixEngine(provider))
    service = AssistantService(
        provider, accounting=accounting, fix_selector=selector,
    )

    response = service.handle(_request("fix_wording"))

    # The response is valid despite the ledger finalization failure.
    assert response.status is AssistantStatus.COMPLETED
    assert response.result
    assert response.result["corrected_text"]
    # Accounting evidence is present but reflects the internal failure.
    evidence = response.accounting
    assert evidence is not None
    assert evidence.operation_id == "req-usage-1"


class _FakeChecker:
    """GrammarChecker fake: canned matches or a forced failure."""

    def __init__(self, matches: list[GrammarMatch] | None = None, fail: bool = False):
        self.matches = matches or []
        self.fail = fail
        self.calls: list[str] = []

    def check(self, text: str) -> list[GrammarMatch]:
        self.calls.append(text)
        if self.fail:
            raise RuntimeError("checker exploded")
        return self.matches


# --- LanguageTool accounting: no reserved attempt leak ------------------


def test_languagetool_fix_wording_does_not_leak_reserved_attempt(
    tmp_path: Path, monkeypatch,
) -> None:
    """When fix_wording uses the LanguageTool engine with accounting enabled,
    no reserved ledger attempt should remain. The response should have
    not_applicable evidence and the ledger should have no entry for this
    request (no attempt was created)."""
    monkeypatch.setenv("AUDISOR_FIX_ENGINE", "languagetool")
    monkeypatch.delenv("AUDISOR_FIX_FALLBACK", raising=False)
    accounting, ledger = _integration(tmp_path)
    provider = _StubProvider()  # never called
    checker = _FakeChecker(
        matches=[GrammarMatch(offset=0, length=4, rule_id="TYPO", message="typo",
                              replacements=("Hello",))]
    )
    grammar_state = GrammarEngineState(engine=LanguageToolFixEngine(checker))
    selector = FixEngineSelector.from_env(
        ModelFixEngine(provider), grammar_state=grammar_state,
    )
    service = AssistantService(
        provider, accounting=accounting, fix_selector=selector,
    )

    response = service.handle(_request("fix_wording", text="helo there"))

    # The response is successful and uses the LanguageTool engine.
    assert response.status is AssistantStatus.COMPLETED
    assert response.engine == "languagetool"
    # Accounting evidence is not_applicable (no model tokens used).
    evidence = response.accounting
    assert evidence is not None
    assert evidence.accounting_status.value == "not_applicable"
    # The provider was never called (LanguageTool handled it).
    assert provider.calls == []
    # No reserved attempt leaked into the ledger.
    record = ledger.get("req-usage-1", ATTEMPT_ID)
    assert record is None, "LanguageTool path must not create a ledger entry"


# --- Malformed model response: preserve provider usage ------------------


def test_fix_wording_malformed_response_preserves_provider_usage(
    tmp_path: Path, monkeypatch,
) -> None:
    """When the model returns malformed JSON but includes usage data,
    the provider usage should be preserved in the accounting evidence
    (not discarded)."""
    for var in ("AUDISOR_FIX_ENGINE", "AUDISOR_FIX_FALLBACK"):
        monkeypatch.delenv(var, raising=False)
    accounting, ledger = _integration(tmp_path)
    # Provider returns malformed JSON with usage data.
    malformed_reply = CompletionReply(
        text="this is not valid json {{{",
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        native_usage=None,
        model="stub-model",
    )
    provider = _StubProvider(reply=malformed_reply)
    selector = FixEngineSelector.from_env(ModelFixEngine(provider))
    service = AssistantService(
        provider, accounting=accounting, fix_selector=selector,
    )

    response = service.handle(_request("fix_wording"))

    # The response failed because the JSON was malformed.
    assert response.status is AssistantStatus.FAILED
    assert response.result["error"]["category"] == "invalid_response"
    # But the accounting evidence preserves the provider's usage data.
    evidence = response.accounting
    assert evidence is not None
    assert evidence.operation_id == "req-usage-1"
    assert evidence.attempt_id == ATTEMPT_ID
    # The accounting status is complete because the provider usage was
    # successfully captured, even though the response body was invalid.
    assert evidence.accounting_status.value == "complete"
    assert evidence.actual is not None
    assert evidence.actual.input_tokens == 10
    assert evidence.actual.output_tokens == 5
    assert evidence.actual.total_tokens == 15
    # The ledger record reached a terminal phase.
    record = ledger.get("req-usage-1", ATTEMPT_ID)
    assert record is not None
    assert record.phase in {"finalized", "blocked"}
