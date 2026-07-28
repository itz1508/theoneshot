"""Fixture 1 — Policy discovery.

The size policy is the authoritative engineering instruction for every
other fixture. This test proves the policy file exists at its
documented location, is readable, and contains the numerical
thresholds the checker enforces.
"""
from __future__ import annotations

from pathlib import Path

_POLICY = (
    Path(__file__).resolve().parents[2] / "docs" / "code-size-policy.md"
)

_EXPECTED_THRESHOLD_MARKERS = (
    "symbol_span` > 60",       # FUNCTION_REVIEW
    "symbol_span` > 100",      # FUNCTION_HARD
    "symbol_span` > 200",      # CLASS_REVIEW
    "physical_lines` > 400",   # PROD_MODULE_REVIEW
    "physical_lines` > 500",   # PROD_MODULE_HARD / TEST_MODULE_REVIEW
)


def test_policy_file_exists_and_is_readable() -> None:
    assert _POLICY.exists(), f"size policy missing at {_POLICY}"
    text = _POLICY.read_text(encoding="utf-8")
    assert text.strip(), "size policy is empty"


def test_policy_documents_every_threshold() -> None:
    text = _POLICY.read_text(encoding="utf-8")
    for marker in _EXPECTED_THRESHOLD_MARKERS:
        assert marker in text, f"policy is missing threshold {marker!r}"


def test_policy_names_the_enforcement_tool() -> None:
    text = _POLICY.read_text(encoding="utf-8")
    assert "check_size.py" in text, (
        "policy does not name the deterministic enforcement tool"
    )
    assert "size_baseline.json" in text, (
        "policy does not name the baseline ratchet file"
    )
