"""Unit tests: request schema validation and unknown-field rejection."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from audisor_assistant.policies.limits import MAX_TEXT_CHARS
from audisor_assistant.schemas.requests import AssistantRequest


def _valid_payload(**overrides):
    payload = {
        "request_id": "req-1",
        "mode": "fix_wording",
        "text": "Some text.",
    }
    payload.update(overrides)
    return payload


def test_valid_request_parses():
    request = AssistantRequest.model_validate(_valid_payload())
    assert request.request_id == "req-1"
    assert request.mode.value == "fix_wording"
    assert request.selected_text is None


def test_request_id_required_and_non_empty():
    with pytest.raises(ValidationError):
        AssistantRequest.model_validate(_valid_payload(request_id=""))
    payload = _valid_payload()
    del payload["request_id"]
    with pytest.raises(ValidationError):
        AssistantRequest.model_validate(payload)


def test_mode_must_be_supported():
    with pytest.raises(ValidationError):
        AssistantRequest.model_validate(_valid_payload(mode="write_poem"))


def test_text_required_and_bounded():
    with pytest.raises(ValidationError):
        AssistantRequest.model_validate(_valid_payload(text=""))
    with pytest.raises(ValidationError):
        AssistantRequest.model_validate(
            _valid_payload(text="x" * (MAX_TEXT_CHARS + 1))
        )


def test_unknown_fields_rejected():
    with pytest.raises(ValidationError):
        AssistantRequest.model_validate(_valid_payload(api_key="sk-nope"))
    with pytest.raises(ValidationError):
        AssistantRequest.model_validate(_valid_payload(provider="cloud"))


def test_credentials_not_accepted_in_body():
    for field in ("authorization", "token", "credential", "password"):
        with pytest.raises(ValidationError):
            AssistantRequest.model_validate(_valid_payload(**{field: "secret"}))
