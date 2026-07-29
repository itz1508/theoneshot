"""Contract tests: live envelopes validate against the shared JSON Schemas.

The shared schemas in ``openai_project/schemas/assistant`` are the
cross-surface contract consumed by the web feature.  These tests prove
the running backend actually emits/accepts what those schemas describe.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from audisor_assistant.application.service import AssistantService
from audisor_assistant.auth.development import DEV_IDENTITY_HEADER, ENVIRONMENT_VAR
from audisor_assistant.domain.modes import AssistantMode
from audisor_assistant.main import create_app
from audisor_assistant.providers.base import DeterministicFakeProvider, ProviderError
from audisor_assistant.schemas.responses import PublicErrorCategory

_SCHEMA_DIR = Path(__file__).resolve().parents[3] / "schemas" / "assistant"

REQUEST_VALIDATOR = Draft202012Validator(
    json.loads((_SCHEMA_DIR / "request.schema.json").read_text(encoding="utf-8"))
)
RESPONSE_VALIDATOR = Draft202012Validator(
    json.loads((_SCHEMA_DIR / "response.schema.json").read_text(encoding="utf-8"))
)

DEV_HEADERS = {DEV_IDENTITY_HEADER: "dev-user"}


@pytest.fixture(autouse=True)
def _development_env(monkeypatch):
    monkeypatch.setenv(ENVIRONMENT_VAR, "development")


def _post(payload: dict, provider=None):
    provider = provider or DeterministicFakeProvider()
    client = TestClient(create_app(AssistantService(provider)))
    return client.post("/v1/assistant/requests", json=payload, headers=DEV_HEADERS)


def test_schemas_are_valid_documents():
    Draft202012Validator.check_schema(REQUEST_VALIDATOR.schema)
    Draft202012Validator.check_schema(RESPONSE_VALIDATOR.schema)


@pytest.mark.parametrize("mode", [m.value for m in AssistantMode])
def test_completed_envelope_matches_response_schema(mode):
    payload = {
        "request_id": "req-schema",
        "mode": mode,
        "text": "hello there",
        "selected_text": "circle back",
    }
    REQUEST_VALIDATOR.validate(payload)
    response = _post(payload)
    assert response.status_code == 200
    RESPONSE_VALIDATOR.validate(response.json())


def test_selection_required_envelope_matches_response_schema():
    payload = {
        "request_id": "req-schema",
        "mode": "translate_slang_jargon",
        "text": "hello there",
    }
    REQUEST_VALIDATOR.validate(payload)
    response = _post(payload)
    RESPONSE_VALIDATOR.validate(response.json())


def test_failure_envelope_matches_response_schema():
    class _FailingProvider:
        provider_id = "stub"
        source = "local"

        def complete(self, request):
            raise ProviderError(PublicErrorCategory.UNAVAILABLE, "down")

    response = _post(
        {"request_id": "req-schema", "mode": "fix_wording", "text": "hi"},
        provider=_FailingProvider(),
    )
    RESPONSE_VALIDATOR.validate(response.json())


def test_request_schema_rejects_unknown_fields():
    errors = list(
        REQUEST_VALIDATOR.iter_errors(
            {
                "request_id": "r",
                "mode": "fix_wording",
                "text": "hi",
                "api_key": "sk-nope",
            }
        )
    )
    assert errors


def test_fix_wording_envelope_engine_matches_result_kind():
    # G11: envelope execution metadata and result semantic shape agree.
    response = _post(
        {"request_id": "req-schema", "mode": "fix_wording", "text": "helo"}
    )
    body = response.json()
    RESPONSE_VALIDATOR.validate(body)
    assert body["engine"] == body["result"]["result_kind"] == "model"
    assert body["fallback_used"] is False


def test_languagetool_envelope_matches_response_schema():
    from audisor_assistant.application.fix_engines import (
        FixEngineSelector,
        GrammarEngineState,
        GrammarMatch,
        LanguageToolFixEngine,
        ModelFixEngine,
    )

    class _Checker:
        def check(self, text):
            return [
                GrammarMatch(
                    offset=0,
                    length=4,
                    rule_id="MORFOLOGIK_RULE_EN_US",
                    message="Possible spelling mistake found.",
                    replacements=("Hello", "Help"),
                )
            ]

    provider = DeterministicFakeProvider()
    selector = FixEngineSelector(
        engine_mode="languagetool",
        fallback="none",
        model_engine=ModelFixEngine(provider),
        grammar_state=GrammarEngineState(engine=LanguageToolFixEngine(_Checker())),
    )
    client = TestClient(
        create_app(AssistantService(provider, fix_selector=selector))
    )
    response = client.post(
        "/v1/assistant/requests",
        json={"request_id": "req-schema", "mode": "fix_wording", "text": "helo there"},
        headers=DEV_HEADERS,
    )
    body = response.json()
    RESPONSE_VALIDATOR.validate(body)
    assert body["engine"] == body["result"]["result_kind"] == "languagetool"
    assert body["provider"] == {"id": "languagetool", "source": "local"}
    assert body["usage"] is None
    change = body["result"]["changes"][0]
    assert change["offset"] == 0 and change["length"] == 4
    assert change["rule_id"] == "MORFOLOGIK_RULE_EN_US"
    assert change["replacements"] == ["Hello", "Help"]


def test_response_schema_rejects_unknown_envelope_fields():
    response = _post(
        {"request_id": "req-schema", "mode": "fix_wording", "text": "hi"}
    )
    body = response.json()
    body["surprise_field"] = True
    assert list(RESPONSE_VALIDATOR.iter_errors(body))


def test_response_without_accounting_field_remains_valid():
    """Backward compatibility: responses emitted before the accounting
    integration (no `accounting` field) still validate."""
    response = _post(
        {"request_id": "req-schema", "mode": "fix_wording", "text": "hi"}
    )
    body = response.json()
    body.pop("accounting", None)
    # No error raised for the missing optional field.
    errors = list(RESPONSE_VALIDATOR.iter_errors(body))
    accounting_errors = [
        e for e in errors if "accounting" in e.json_path or "accounting" in str(e.path)
    ]
    assert not accounting_errors


def test_complete_accounting_evidence_validates():
    body = {
        "request_id": "r",
        "mode": "fix_wording",
        "status": "completed",
        "result": {"corrected_text": "ok"},
        "warnings": [],
        "uncertainty": [],
        "accounting": {
            "schema_version": "1.0.0",
            "operation_id": "op.1",
            "attempt_id": "attempt.1",
            "provider": "fake-deterministic",
            "model": "fake-deterministic",
            "accounting_status": "complete",
            "estimate": {
                "input_tokens": 10,
                "reserved_output_tokens": 5,
                "estimated_total_tokens": 15,
                "source": "approximate",
                "confidence": "approximate",
                "method": "character_ratio",
            },
            "actual": {
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": 15,
                "source": "provider_reported",
                "status": "complete",
            },
            "estimated_cost": {
                "currency": "USD",
                "uncached_input": "0.000010",
                "cached_read": "0.000000",
                "cached_write": "0.000000",
                "output": "0.000015",
                "reasoning": "0.000000",
                "total": "0.000025",
                "pricing_record_id": "v1",
                "pricing_snapshot_sha256": "sha256:" + "a" * 64,
            },
            "actual_cost": None,
            "warnings": ["pricing_unavailable"],
        },
    }
    RESPONSE_VALIDATOR.validate(body)


def test_not_applicable_accounting_evidence_validates():
    body = {
        "request_id": "r",
        "mode": "fix_wording",
        "status": "completed",
        "result": {"corrected_text": "ok"},
        "warnings": [],
        "uncertainty": [],
        "accounting": {
            "schema_version": "1.0.0",
            "operation_id": "op.lt",
            "attempt_id": "attempt.1",
            "provider": "languagetool",
            "model": "not_applicable",
            "accounting_status": "not_applicable",
            "warnings": [],
        },
    }
    RESPONSE_VALIDATOR.validate(body)


def test_accounting_evidence_rejects_unknown_nested_fields():
    body = {
        "request_id": "r",
        "mode": "fix_wording",
        "status": "completed",
        "result": {"corrected_text": "ok"},
        "warnings": [],
        "uncertainty": [],
        "accounting": {
            "schema_version": "1.0.0",
            "operation_id": "op.1",
            "attempt_id": "attempt.1",
            "provider": "p",
            "model": "m",
            "accounting_status": "complete",
            "warnings": [],
            "surprise_nested": True,
        },
    }
    assert list(RESPONSE_VALIDATOR.iter_errors(body))


def test_accounting_evidence_rejects_invalid_schema_version():
    body = {
        "request_id": "r",
        "mode": "fix_wording",
        "status": "completed",
        "result": {"corrected_text": "ok"},
        "warnings": [],
        "uncertainty": [],
        "accounting": {
            "schema_version": "9.9.9",
            "operation_id": "op.1",
            "attempt_id": "attempt.1",
            "provider": "p",
            "model": "m",
            "accounting_status": "complete",
            "warnings": [],
        },
    }
    assert list(RESPONSE_VALIDATOR.iter_errors(body))
