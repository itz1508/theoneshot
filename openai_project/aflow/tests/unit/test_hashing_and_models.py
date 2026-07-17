from __future__ import annotations

from copy import deepcopy

import pytest

from aflow.analysis.decision_engine import analyze
from aflow.domain.models import DomainInvariantError, validate_domain_invariants
from aflow.fixtures.factory import fixed_clock, request_bundle
from aflow.storage.hashing import canonical_hash, seal, verify_hash


def test_canonical_hash_is_unicode_safe_and_key_order_independent():
    left = {"z": "café 東京", "a": [1, 2]}
    right = {"a": [1, 2], "z": "café 東京"}
    assert canonical_hash(left) == canonical_hash(right)


def test_seal_hash_cannot_be_bypassed():
    sealed = seal({"value": "one"})
    assert verify_hash(sealed)
    sealed["value"] = "two"
    assert not verify_hash(sealed)


def test_outer_hash_binds_nested_artifact_reference_hashes():
    sealed = seal({"reference": {"artifact_id": "artifact.one", "content_hash": canonical_hash("one")}})
    sealed["reference"]["content_hash"] = canonical_hash("two")
    assert not verify_hash(sealed)


def test_corrupted_clean_decision_is_rejected_on_load_semantics():
    decision = analyze(request_bundle(), clock=fixed_clock)
    corrupt = deepcopy(decision)
    corrupt["blocking"] = True
    corrupt = seal(corrupt)
    with pytest.raises((DomainInvariantError, ValueError)):
        validate_domain_invariants(corrupt, "analysis_decision")


def test_corrupted_blocking_decision_is_rejected():
    request = request_bundle()
    request["plan"]["actions"][0]["phase"] = "design"
    decision = analyze(request, clock=fixed_clock)
    corrupt = deepcopy(decision)
    corrupt["findings"] = []
    corrupt = seal(corrupt)
    with pytest.raises((DomainInvariantError, ValueError)):
        validate_domain_invariants(corrupt, "analysis_decision")
