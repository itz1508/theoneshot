"""Repository-local A-Flow lifecycle adapter.

This module deliberately does not import or modify the frozen A-Flow package.
It gives primary Codex deterministic lock, guard, and completion primitives
around the frozen package's documented analysis/evaluation interfaces.
"""

from .contract import (
    AflowLifecycleError,
    accept_for_primary,
    canonical_text,
    frozen_tree_digest,
    frozen_readiness_decision,
    normalize_frozen_readiness,
    requires_aflow_analysis,
    verify_lock,
    write_lock,
)
from .adapter import assemble_contract, verify_contract
from .ignition import IgnitionResult, ignite, is_mutation_task, select_candidate_plan

__all__ = [
    "AflowLifecycleError",
    "accept_for_primary",
    "canonical_text",
    "frozen_tree_digest",
    "frozen_readiness_decision",
    "normalize_frozen_readiness",
    "requires_aflow_analysis",
    "verify_lock",
    "write_lock",
    "assemble_contract",
    "verify_contract",
    "IgnitionResult",
    "ignite",
    "is_mutation_task",
    "select_candidate_plan",
]
