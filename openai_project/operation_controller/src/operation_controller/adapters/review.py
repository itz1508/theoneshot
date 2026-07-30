"""Canonical A-Flow review adapter for OperationController."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from audisor.audisor_lifecycle.artifact_flow import run_artifact_lifecycle
from audisor.audisor_lifecycle.management import list_issues, resolve_stage_providers
from audisor.audisor_lifecycle.persistence import default_state_root
from audisor.audisor_lifecycle.stage_worker import ManagedStageWorker


class CanonicalAflowReviewAdapter:
    """Submit operation plans to the runtime-owned canonical lifecycle."""

    def __init__(self, *, state_root: Path | None = None) -> None:
        self._state_root = state_root or default_state_root()

    def review(
        self,
        plan: dict[str, Any],
        operation_id: str,
        plan_digest: str = "",
        authority_scope: dict[str, Any] | None = None,
        provider_override: str | None = None,
    ) -> dict[str, Any]:
        content = json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True)
        digest = plan_digest or hashlib.sha256(content.encode("utf-8")).hexdigest()
        context = json.dumps(
            {
                "operation_id": operation_id,
                "authority_scope": authority_scope or {},
                "source": "operation_controller",
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        trigger = {
            "artifact_id": f"operation.{operation_id}.plan",
            "artifact_type": "implementation_plan",
            "status": "draft_complete",
            "content": content,
            "intent": "Qualify the operation plan before execution.",
            "context": context,
        }

        worker = None
        if provider_override is not None:
            primary, fallback, fallback_ready = resolve_stage_providers(
                state_root=self._state_root
            )
            if provider_override == "local-openai-compatible":
                worker = ManagedStageWorker(primary=primary)
            elif provider_override == "fireworks" and fallback is not None and fallback_ready:
                worker = ManagedStageWorker(primary=fallback)
            else:
                return {
                    "decision": "provider_error",
                    "findings": [],
                    "plan_digest": digest,
                    "issue_id": None,
                    "error": "requested provider is not ready",
                }

        lifecycle = run_artifact_lifecycle(
            trigger,
            worker=worker,
            state_root=self._state_root,
        )
        issue_id = None
        if lifecycle.get("status") == "error":
            issue_page = list_issues(state_root=self._state_root, limit=100)
            for issue in issue_page["items"]:
                if (
                    issue.get("lifecycle_run_id") == lifecycle.get("lifecycle_run_id")
                    or (
                        issue.get("artifact_id") == trigger["artifact_id"]
                        and issue.get("stage") == lifecycle.get("stage")
                    )
                ):
                    issue_id = issue.get("issue_id")
                    break
        common = {
            "plan_digest": digest,
            "review_id": lifecycle.get("lifecycle_run_id"),
            "lifecycle_result": lifecycle,
            "issue_id": issue_id,
        }
        if lifecycle.get("status") == "improved":
            try:
                improved_plan = json.loads(lifecycle["improved_artifact"])
            except (KeyError, TypeError, json.JSONDecodeError):
                improved_plan = plan
            return {
                **common,
                "decision": "no_material_gap",
                "findings": [],
                "improved_plan": improved_plan,
            }
        if lifecycle.get("status") == "unresolved_gap":
            return {
                **common,
                "decision": "unresolved_gap",
                "findings": lifecycle.get("unresolved_gaps", []),
            }
        return {
            **common,
            "decision": "provider_error",
            "findings": [],
            "error": lifecycle.get("detail", "A-Flow lifecycle error"),
        }


class StubReviewAdapter:
    """Test-only compatibility adapter; production construction must not use it."""

    def review(
        self,
        plan: dict[str, Any],
        operation_id: str,
        plan_digest: str = "",
        authority_scope: dict[str, Any] | None = None,
        provider_override: str | None = None,
    ) -> dict[str, Any]:
        content = json.dumps(plan, sort_keys=True)
        digest = plan_digest or hashlib.sha256(content.encode()).hexdigest()[:16]
        return {
            "decision": "no_material_gap",
            "findings": [],
            "review_id": f"stub-{operation_id}-{digest[:8]}",
            "plan_digest": digest,
            "snapshot_id": None,
        }
