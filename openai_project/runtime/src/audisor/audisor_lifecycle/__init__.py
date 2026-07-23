"""Repository-local Audisor lifecycle adapter.

This module deliberately does not import or modify the frozen Audisor package.
It gives primary Codex deterministic lock, guard, and completion primitives
around the frozen package's documented analysis/evaluation interfaces.
"""

from .contract import (
    AudisorLifecycleError,
    accept_for_primary,
    canonical_text,
    frozen_tree_digest,
    frozen_readiness_decision,
    normalize_frozen_readiness,
    requires_audisor_analysis,
    verify_lock,
    write_lock,
)
from .adapter import assemble_contract, verify_contract
from .ignition import IgnitionResult, ignite, is_mutation_task, select_candidate_plan
from .plan_trigger import auto_trigger_plan_review
from .analysis_package import (
    AnalysisPackageError,
    FrozenAnalysisPackage,
    assemble_analysis_package,
    package_from_context,
    validate_analysis_request,
)
from .active_state import (
    clear_active_state,
    read_active_state,
    write_active_state,
)
from .review_contract import (
    build_analysis_for_lock,
    map_decision_to_frozen,
    review_and_lock,
)

__all__ = [
    "AudisorLifecycleError",
    "accept_for_primary",
    "canonical_text",
    "frozen_tree_digest",
    "frozen_readiness_decision",
    "normalize_frozen_readiness",
    "requires_audisor_analysis",
    "verify_lock",
    "write_lock",
    "assemble_contract",
    "verify_contract",
    "IgnitionResult",
    "ignite",
    "is_mutation_task",
    "select_candidate_plan",
    "auto_trigger_plan_review",
    "AnalysisPackageError",
    "FrozenAnalysisPackage",
    "assemble_analysis_package",
    "package_from_context",
    "validate_analysis_request",
    "clear_active_state",
    "read_active_state",
    "write_active_state",
    "build_analysis_for_lock",
    "map_decision_to_frozen",
    "review_and_lock",
]
