"""Unit tests: privacy policy — sanitized errors, records, diagram code."""
from __future__ import annotations

from datetime import datetime, timezone

from audisor_assistant.policies.privacy import (
    sanitize_diagram_code,
    sanitize_public_message,
    sanitized_request_record,
)


def test_sanitize_public_message_strips_credentials_and_paths():
    message = (
        "call failed: Bearer sk-abc123 at C:\\secret\\keys.txt "
        "with api_key=xyz under /etc/passwd"
    )
    cleaned = sanitize_public_message(message)
    assert "sk-abc123" not in cleaned
    assert "C:\\secret" not in cleaned
    assert "api_key=xyz" not in cleaned
    assert "/etc/passwd" not in cleaned


def test_sanitize_diagram_code_removes_click_and_script():
    code = (
        "flowchart TD\n"
        "  A[Client] --> B[API]\n"
        "  click A \"javascript:alert(1)\"\n"
        "<script>alert(1)</script>"
    )
    cleaned, warnings = sanitize_diagram_code(code)
    assert "click" not in cleaned
    assert "script" not in cleaned.lower()
    assert "A[Client] --> B[API]" in cleaned
    assert warnings


def test_sanitized_record_excludes_user_text():
    record = sanitized_request_record(
        request_id="req-9",
        mode="fix_wording",
        status="completed",
        provider_source="local",
        started_at=datetime.now(timezone.utc),
        usage={"total_tokens": 3},
    )
    assert set(record) == {
        "request_id",
        "mode",
        "status",
        "provider_source",
        "started_at",
        "completed_at",
        "usage",
    }
