"""Compatibility adapter for the runtime lifecycle surface.

Single backend-owned seam over ``audisor.audisor_lifecycle``. The Fix host
imports lifecycle symbols from here instead of from the tombstoned runtime
directly, so a later lifecycle-ownership migration retargets exactly one
module. Pure re-exports only: ``fix_host`` compares igniter identity
(``aflow_igniter is ignite``), so every name must stay the same object as
the runtime's — never a wrapper.
"""

from audisor.audisor_lifecycle.artifacts import audisor_operation_artifact
from audisor.audisor_lifecycle.analysis_package import AnalysisPackageError, package_from_context
from audisor.audisor_lifecycle.ignition import ignite
from audisor.audisor_lifecycle.operation import (
    AudisorOperationContext,
    FrozenAudisorPolicy,
    make_operation_context,
    read_frozen_audisor_policy,
)

__all__ = [
    "AnalysisPackageError",
    "AudisorOperationContext",
    "FrozenAudisorPolicy",
    "audisor_operation_artifact",
    "ignite",
    "make_operation_context",
    "package_from_context",
    "read_frozen_audisor_policy",
]
