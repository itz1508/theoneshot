"""Stage execution: timeout enforcement, recorded repair, strict validation."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Any, Mapping

from audisor.workers.base import ProviderError

from .output_processing import _prune_to_schema
from .stage_contracts import (
    STAGE_OUTPUT_SCHEMAS,
    StageWorker,
    _MAX_REPAIRED_FIELDS,
    _REPAIRABLE_STAGES,
    _STAGE_VALIDATORS,
)


def _call_with_timeout(worker: StageWorker, stage: str, payload: Mapping[str, Any], timeout: float | None) -> Mapping[str, Any]:
    if timeout is None or timeout <= 0:
        return worker.run_stage(stage, payload)
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"aflow-{stage}")
    try:
        future = executor.submit(worker.run_stage, stage, payload)
        return future.result(timeout=timeout)
    finally:
        executor.shutdown(wait=False)


def _execute_stage(
    worker: StageWorker,
    stage: str,
    payload: Mapping[str, Any],
    stage_timeout_seconds: float | None,
    repairs: list[dict[str, Any]],
) -> Mapping[str, Any] | dict[str, Any]:
    """Run one stage call: bounded errors, recorded repair, strict validation.

    Every failure mode becomes an ``error`` result dict — a malformed or
    late worker answer can never cause a stage transition. Recorded removals
    are appended to ``repairs`` so the finish contract can report them.
    """
    try:
        output = _call_with_timeout(worker, stage, payload, stage_timeout_seconds)
    except FutureTimeoutError:
        return {
            "status": "error",
            "stage": stage,
            "issue_code": "provider_timeout",
            "detail": f"stage timed out after {stage_timeout_seconds}s",
        }
    except ProviderError as exc:
        return {
            "status": "error",
            "stage": stage,
            "issue_code": exc.code,
            "detail": str(exc),
            "provider_error_detail": exc.internal_detail,
        }
    except Exception as exc:  # transport/provider/worker failure -> bounded error
        return {
            "status": "error",
            "stage": stage,
            "issue_code": "unknown",
            "detail": f"{type(exc).__name__}: {exc}",
        }
    if not isinstance(output, Mapping):
        return {
            "status": "error",
            "stage": stage,
            "issue_code": "stage_schema_error",
            "detail": "worker output is not an object",
        }
    if stage in _REPAIRABLE_STAGES:
        # Limited recorded repair: unknown fields are removed before
        # validation, every removal is preserved in the result, and
        # excessive removal is treated as malformed, not repairable.
        repaired, removed = _prune_to_schema(STAGE_OUTPUT_SCHEMAS[stage], dict(output))
        if len(removed) > _MAX_REPAIRED_FIELDS:
            return {
                "status": "error",
                "stage": stage,
                "issue_code": "stage_schema_error",
                "detail": (
                    f"worker output required removing {len(removed)} unknown fields "
                    f"(ceiling {_MAX_REPAIRED_FIELDS}); treating as malformed"
                ),
            }
        if removed:
            repairs.append({"stage": stage, "removed_fields": removed})
    else:
        # Authoritative stages are validated strictly as emitted.
        repaired = dict(output)
    errors = sorted(_STAGE_VALIDATORS[stage].iter_errors(repaired), key=lambda e: e.json_path)
    if errors:
        first = errors[0]
        return {
            "status": "error",
            "stage": stage,
            "issue_code": "stage_schema_error",
            "detail": f"worker output failed stage schema at {first.json_path}: {first.message}",
        }
    return repaired
