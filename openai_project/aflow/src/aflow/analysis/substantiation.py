from __future__ import annotations

from typing import Any
import re

from aflow.schemas.validator import SchemaValidationError, validate


VAGUE = {
    "validation might be incomplete",
    "there may be a problem",
    "consider adding more tests",
    "this could fail",
}
STOPWORDS = {"this", "that", "with", "from", "into", "only", "plan", "validation", "finding", "might", "could", "would", "required", "evidence"}


def _tokens(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", text.lower()) if len(token) >= 4 and token not in STOPWORDS}


def _resolve_pointer(root: Any, pointer: str) -> bool:
    if pointer == "":
        return True
    current = root
    try:
        for raw in pointer.lstrip("/").split("/"):
            token = raw.replace("~1", "/").replace("~0", "~")
            current = current[int(token)] if isinstance(current, list) else current[token]
    except (KeyError, IndexError, TypeError, ValueError):
        return False
    return True


def substantiate(
    candidates: list[dict[str, Any]], request: dict[str, Any], *, plan_locations_are_evidence: bool = False
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    requirement_ids = {item["requirement_id"] for item in request["success_definition"]["requirements"]}
    evidence = {item["evidence_id"]: item["content_hash"] for item in request["evidence"]}
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for candidate in candidates:
        reasons: list[str] = []
        try:
            validate(candidate, "finding.schema.json")
        except SchemaValidationError as exc:
            reasons.append(str(exc))
        if candidate.get("origin") != "semantic":
            reasons.append("semantic adapter may only propose semantic findings")
        if candidate.get("specific_claim", "").strip().lower() in VAGUE or len(candidate.get("specific_claim", "")) < 16:
            reasons.append("claim is vague or insufficiently specific")
        if any(req not in requirement_ids for req in candidate.get("requirement_references", [])):
            reasons.append("finding references a requirement outside the locked success definition")
        for location in candidate.get("plan_locations", []):
            scoped = location.removeprefix("/plan") if location.startswith("/plan") else location
            if not _resolve_pointer(request["plan"], scoped):
                reasons.append(f"plan location does not resolve: {location}")
        for ref in candidate.get("evidence_references", []):
            if evidence.get(ref.get("evidence_id")) != ref.get("content_hash"):
                reasons.append(f"evidence reference is absent or hash-mismatched: {ref.get('evidence_id')}")
        if not candidate.get("evidence_references"):
            reasons.append("finding has no bounded evidence")
        else:
            claim_tokens = _tokens(" ".join([
                candidate.get("specific_claim", ""), candidate.get("reasoning", ""), candidate.get("why_it_matters", "")
            ]))
            cited = [item for item in request["evidence"] if any(
                ref.get("evidence_id") == item["evidence_id"] and ref.get("content_hash") == item["content_hash"]
                for ref in candidate.get("evidence_references", [])
            )]
            supports = any(
                len(claim_tokens & _tokens(f"{item.get('claim', '')} {item.get('payload_summary', '')}")) >= 2
                or (
                    item.get("locator", {}).get("kind") == "json_pointer"
                    and item.get("locator", {}).get("value") in candidate.get("plan_locations", [])
                )
                for item in cited
            )
            if not supports and not (plan_locations_are_evidence and candidate.get("plan_locations")):
                reasons.append("cited evidence does not materially support the finding claim")
        if reasons:
            rejected.append({
                "finding_id": candidate.get("finding_id", "finding.invalid"),
                "reason": "; ".join(dict.fromkeys(reasons)),
                "evidence_references": [
                    ref for ref in candidate.get("evidence_references", [])
                    if evidence.get(ref.get("evidence_id")) == ref.get("content_hash")
                ],
            })
        else:
            accepted.append(candidate)
    return accepted, rejected
