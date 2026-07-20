"""Host boundary for automatic Audisor gating of the `audisor run` command."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from audisor.audisor_lifecycle.ignition import ignite, IgnitionResult
from audisor.audisor_lifecycle.operation import FrozenAudisorPolicy, read_frozen_audisor_policy
from audisor.schemas.task_input import TaskInput
from audisor.schemas.task_output import TaskOutput


@dataclass(frozen=True)
class AudisorGateResult:
    """Result of the Audisor preflight gate for `audisor run`."""

    permitted: bool
    reason: str
    ignition_result: IgnitionResult | None = None


def write_failure_results(path: Path, tasks: list[TaskInput], reason: str) -> None:
    """Atomically write a results.json where every task reports the Audisor failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    results = [
        TaskOutput(task_id=task.task_id, answer=f"Audisor rejected: {reason}")
        for task in tasks
    ]
    payload = json.dumps(
        [result.model_dump() for result in results],
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def check_aflow_gate(
    tasks: list[TaskInput],
    *,
    policy: FrozenAudisorPolicy | None = None,
    worker: Any | None = None,
    task_kind: str = "batch_execution",
    task: Mapping[str, Any] | None = None,
    repository_context: Mapping[str, Any] | None = None,
    supplied_plan: Mapping[str, Any] | None = None,
    create_candidate_plan: Any | None = None,
    invoke_audisor_analysis: Any | None = None,
) -> AudisorGateResult:
    """Call ignite() once when Audisor is enabled and decide whether to continue.

    - Audisor disabled → permitted=True.
    - Audisor enabled, ignite() raises → permitted=False.
    - Audisor enabled, lifecycle_selected=False → permitted=True (non-mutation task).
    - Audisor enabled, lifecycle_selected=True, implementation_eligible=True → permitted=True.
    - Audisor enabled, lifecycle_selected=True, implementation_eligible=False → permitted=False.
    """
    frozen_policy = policy or read_frozen_audisor_policy()
    if not frozen_policy.enabled:
        return AudisorGateResult(permitted=True, reason="Audisor is disabled")

    task_payload = task if task is not None else {
        "task_count": len(tasks),
        "task_ids": [t.task_id for t in tasks],
    }
    repo_context = repository_context if repository_context is not None else {}

    try:
        result = ignite(
            policy=frozen_policy,
            worker=worker,
            task_kind=task_kind,
            task=task_payload,
            repository_context=repo_context,
            supplied_plan=supplied_plan,
            create_candidate_plan=create_candidate_plan,
            invoke_audisor_analysis=invoke_audisor_analysis,
        )
    except Exception as exc:
        return AudisorGateResult(
            permitted=False,
            reason=f"Audisor ignition failed: {type(exc).__name__}: {exc}",
        )

    if not result.lifecycle_selected:
        return AudisorGateResult(
            permitted=True,
            reason="Audisor did not select this task for lifecycle gating",
            ignition_result=result,
        )

    if result.implementation_eligible:
        return AudisorGateResult(
            permitted=True,
            reason="Audisor accepted",
            ignition_result=result,
        )

    return AudisorGateResult(
        permitted=False,
        reason="Audisor rejected: implementation not eligible",
        ignition_result=result,
    )