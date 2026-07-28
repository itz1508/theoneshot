"""Repository-local Audisor lifecycle adapter.

This module deliberately does not import or modify the frozen Audisor package.
It exposes the canonical artifact lifecycle engine plus the legacy contract
primitives still consumed by the phased-out ignition path.
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
from .analysis_package import (
    AnalysisPackageError,
    FrozenAnalysisPackage,
    assemble_analysis_package,
    package_from_context,
    validate_analysis_request,
)
from .artifact_flow import (
    RESULT_STATUSES,
    STAGES,
    LocalStageWorker,
    PersistedResultError,
    StageOutputError,
    StageWorker,
    read_last_result,
    run_artifact_lifecycle,
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
    "AnalysisPackageError",
    "FrozenAnalysisPackage",
    "assemble_analysis_package",
    "package_from_context",
    "validate_analysis_request",
    "RESULT_STATUSES",
    "STAGES",
    "LocalStageWorker",
    "PersistedResultError",
    "StageOutputError",
    "StageWorker",
    "read_last_result",
    "run_artifact_lifecycle",
]
