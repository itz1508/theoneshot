from __future__ import annotations

import re
from typing import Any


DIMENSIONS = (
    "correctness", "completeness", "usability", "consistency", "reliability",
    "evidence_quality", "constraint_compliance",
)
RANK = {"fail": 0, "unproven": 1, "partial": 2, "pass": 3, "not_applicable": 4}


def explicit_quality(evidence: list[dict[str, Any]], dimension: str) -> str | None:
    pattern = re.compile(rf"(?:quality:)?{re.escape(dimension)}\s*=\s*(pass|partial|fail|unproven)", re.I)
    found: list[str] = []
    for item in evidence:
        text = f"{item.get('claim', '')} {item.get('payload_summary', '')}"
        match = pattern.search(text)
        if match:
            found.append(match.group(1).lower())
    return min(found, key=RANK.get) if found else None


def is_trusted_evidence(item: dict[str, Any]) -> bool:
    return (
        item.get("source", {}).get("evidence_status") == "verified"
        and item.get("collected_by") in {"deterministic_local", "external_harness", "human"}
        and item.get("visibility") == "build_result_input"
    )


def aggregate_quality(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for dimension in DIMENSIONS:
        rows = [q for entry in entries for q in entry["quality_results"] if q["dimension"] == dimension]
        applicable = [row for row in rows if row["status"] != "not_applicable"]
        if not applicable:
            status, reason, refs = "not_applicable", "No locked requirement declared this dimension applicable.", []
        else:
            worst = min(applicable, key=lambda item: RANK[item["status"]])
            status, reason, refs = worst["status"], worst["reason"], worst["evidence_references"]
        result.append({"dimension": dimension, "status": status, "reason": reason, "evidence_references": refs})
    return result
