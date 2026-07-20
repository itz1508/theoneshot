"""Fix v1.0 constants. These are intentionally not shared with Build."""

COMPLETENESS_THRESHOLD = 0.9391
AFLOW_GAP_CORRECTION_ATTEMPTS = 1
PRESIM_RESCORE_ATTEMPTS = 1
MAX_AFLOW_INVOCATIONS_PER_OPERATION = 1
COMPLETENESS_WEIGHTS = {
    "scope_completeness": 0.75,
    "dependency_resolvability": 0.25,
}
STRUCTURAL_GATES = (
    "plan_completeness",
    "aflow_output_completeness",
    "statement_consistency",
    "dependency_integrity",
)

