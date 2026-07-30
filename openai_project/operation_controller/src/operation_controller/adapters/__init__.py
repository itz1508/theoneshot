"""Adapter implementations for OperationController.

Provides concrete adapters that use the shared ToolLoop utility:
- PlanningAdapter: read-only tool loop for plan generation
- CanonicalAflowReviewAdapter: runtime-owned A-Flow review
- ExecutionAdapter: mutation-capable tool loop for plan execution
"""
from operation_controller.adapters.planning import LLMPlanningAdapter
from operation_controller.adapters.review import CanonicalAflowReviewAdapter, StubReviewAdapter
from operation_controller.adapters.execution import LLMExecutionAdapter

__all__ = [
    "LLMPlanningAdapter",
    "CanonicalAflowReviewAdapter",
    "StubReviewAdapter",
    "LLMExecutionAdapter",
]
