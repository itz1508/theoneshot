from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class SemanticReviewer(Protocol):
    def review(self, request: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class NullSemanticReviewer:
    adapter_id: str = "adapter.null"

    def review(self, request: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": "1.0.0",
            "review_id": f"review.{request['analysis_id']}",
            "analysis_id": request["analysis_id"],
            "reviewer": {"adapter_id": self.adapter_id, "model_or_agent_id": "deterministic-null"},
            "candidate_findings": [],
            "review_summary": "No semantic candidates proposed by the neutral deterministic adapter.",
        }


@dataclass(frozen=True)
class StaticSemanticReviewer:
    candidate_findings: tuple[dict[str, Any], ...]
    adapter_id: str = "adapter.static"

    def review(self, request: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": "1.0.0",
            "review_id": f"review.{request['analysis_id']}",
            "analysis_id": request["analysis_id"],
            "reviewer": {"adapter_id": self.adapter_id, "model_or_agent_id": "deterministic-static"},
            "candidate_findings": [dict(item) for item in self.candidate_findings],
            "review_summary": "Static candidates for deterministic substantiation.",
        }
