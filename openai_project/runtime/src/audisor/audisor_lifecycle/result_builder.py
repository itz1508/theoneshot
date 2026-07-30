"""Lifecycle run state and the finish contract that stamps every result."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .management import create_root_cause_issue, persist_run_evidence
from .persistence import _persist_result
from .stage_contracts import REPAIR_POLICY


@dataclass
class _LifecycleRunState:
    """Mutable identity and evidence state shared by one lifecycle run.

    ``finish`` is the single exit contract: repair evidence, runtime-derived
    identity, persistence, and active-run marker cleanup are applied to every
    result that reaches it, so no exit path can drop part of the contract.
    """

    root: Path
    artifact_id: Any
    valid_id: bool
    repairs: list[dict[str, Any]] = field(default_factory=list)
    repair_cycles: int = 0
    evaluation_repair: dict[str, Any] | None = None
    digest: str | None = None
    content_digest: str | None = None
    revision: int | None = None
    run_id: str | None = None
    started_at: str | None = None
    marker_path: Path | None = None
    trigger: Mapping[str, Any] | None = None
    provider_attempts: list[dict[str, Any]] = field(default_factory=list)

    def finish(
        self,
        result: dict[str, Any],
        *,
        persist_result: bool = True,
    ) -> dict[str, Any]:
        if self.repairs:
            # Repair is explicit, never silent: the result names every field
            # that normalization removed, with its original value — always
            # the full trio, never a partial claim.
            result["output_repaired"] = True
            result["output_repairs"] = self.repairs
            result["repair_policy"] = REPAIR_POLICY
        if self.digest is not None:
            # Runtime-derived identity: never agent-authored trigger fields,
            # and always present once a lifecycle has started.
            result["submission_disposition"] = "started"
            result["submission_digest"] = self.digest
            result["content_digest"] = self.content_digest
            result["artifact_revision"] = self.revision
            result["lifecycle_run_id"] = self.run_id
            result["started_at"] = self.started_at
            result["completed_at"] = datetime.now(timezone.utc).isoformat()
            # Explicit 0 | 1: absence never carries hidden meaning.
            result["evaluation_repair_cycles"] = self.repair_cycles
            if self.evaluation_repair is not None:
                # The bounded cycle leaves evidence, not just a counter.
                result["evaluation_repair"] = self.evaluation_repair
        if self.valid_id:
            result["artifact_id"] = self.artifact_id
        persist_run_evidence(self.root, result, self.provider_attempts)
        if result.get("status") == "error" and self.trigger is not None:
            create_root_cause_issue(
                self.root, result, self.trigger, self.provider_attempts
            )
        # Provider internal detail is evidence for issue construction only;
        # it is never returned through the artifact result contract.
        result.pop("provider_error_detail", None)
        result.pop("issue_code", None)
        if self.valid_id and persist_result:
            _persist_result(self.root, self.artifact_id, result)
        if self.marker_path is not None:
            try:
                self.marker_path.unlink(missing_ok=True)
            except OSError:
                pass
        return result
