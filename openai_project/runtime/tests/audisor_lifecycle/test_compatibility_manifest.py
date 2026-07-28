"""Compatibility-manifest comparison: any unapproved contract drift fails.

The stored fixture freezes the lifecycle contract captured before the
refactor. Regenerating the manifest from the current code and comparing
key-by-key proves that schemas, prompts, examples, constants, ordering,
digest rules, and representative result structures are unchanged.
"""
from __future__ import annotations

import json
from pathlib import Path

from .manifest_builder import build_manifest

_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "aflow_compatibility_manifest.json"
)


def test_compatibility_manifest_matches_stored_fixture() -> None:
    stored = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    current = build_manifest()
    # Compare section by section so a drift names its contract area.
    assert sorted(current) == sorted(stored)
    for key in stored:
        assert current[key] == stored[key], f"compatibility drift in manifest section {key!r}"
