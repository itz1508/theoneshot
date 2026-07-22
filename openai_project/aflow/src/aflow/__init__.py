"""A-Flow: plan analysis and output-evidence evaluation without execution."""

from .analysis.decision_engine import analyze
from .evaluation.final_decision import evaluate_result

__all__ = ["analyze", "evaluate_result"]
__version__ = "0.9.0"

