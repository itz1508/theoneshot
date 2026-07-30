"""Provider-routed A-Flow stage workers with bounded fallback evidence."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from audisor.schemas.task_input import TaskInput
from audisor.workers.base import (
    ProviderError,
    ProviderInvalidResponseError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    WorkerProvider,
)
from audisor.workers.local import LocalWorker

from .operation import FrozenAudisorPolicy
from .output_processing import _drop_empty_optionals, _parse_json_object, _prune_to_schema
from .stage_contracts import STAGE_OUTPUT_SCHEMAS, _STAGE_VALIDATORS
from .stage_prompts import _STAGE_INSTRUCTIONS, _STAGE_OUTPUT_EXAMPLES


@dataclass
class LocalStageWorker:
    """StageWorker implementation over the local OpenAI-compatible endpoint."""

    worker: LocalWorker

    @classmethod
    def from_policy(cls, policy: FrozenAudisorPolicy) -> "LocalStageWorker":
        return cls(
            worker=LocalWorker(
                base_url=policy.base_url,
                model_id=policy.model_id,
                timeout_seconds=policy.timeout_seconds,
                structured_output=True,
            )
        )

    def run_stage(self, stage_name: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        prompt = "\n\n".join(
            (
                _STAGE_INSTRUCTIONS[stage_name],
                "Respond with one JSON object in exactly this shape (fill the "
                "placeholder values; add or repeat array items as needed; no "
                "other keys, no prose outside the JSON):",
                _STAGE_OUTPUT_EXAMPLES[stage_name],
                "Input payload:",
                json.dumps(dict(payload), ensure_ascii=False, indent=2, default=str),
            )
        )
        output = self.worker.execute(TaskInput(task_id=f"aflow-{stage_name}", prompt=prompt))
        return _drop_empty_optionals(stage_name, _parse_json_object(output.answer))


_FALLBACK_ELIGIBLE = (
    ProviderUnavailableError,
    ProviderTimeoutError,
    ProviderRateLimitedError,
    ProviderInvalidResponseError,
)


@dataclass
class ManagedStageWorker:
    """Run a stage on the primary provider and at most one ready fallback."""

    primary: WorkerProvider
    fallback: WorkerProvider | None = None
    fallback_ready: bool = False
    progress: Callable[[str, str, int, float, float], None] | None = None

    def __post_init__(self) -> None:
        self.attempts: list[dict[str, Any]] = []

    @staticmethod
    def _prompt(stage_name: str, payload: Mapping[str, Any]) -> str:
        return "\n\n".join(
            (
                _STAGE_INSTRUCTIONS[stage_name],
                "Respond with one JSON object in exactly this shape (fill the "
                "placeholder values; add or repeat array items as needed; no "
                "other keys, no prose outside the JSON):",
                _STAGE_OUTPUT_EXAMPLES[stage_name],
                "Input payload:",
                json.dumps(dict(payload), ensure_ascii=False, indent=2, default=str),
            )
        )

    @staticmethod
    def _model(provider: WorkerProvider) -> str:
        return str(getattr(provider, "model_id", None) or getattr(provider, "model", ""))

    def _run_attempt(
        self,
        provider: WorkerProvider,
        stage_name: str,
        prompt: str,
        *,
        attempt: int,
        selection_reason: str,
        fallback_usage: bool,
    ) -> Mapping[str, Any]:
        started = time.monotonic()
        provider_id = provider.provider_id
        budget = float(getattr(provider, "timeout_seconds", 0.0) or 0.0)
        if self.progress:
            self.progress(stage_name, provider_id, attempt, 0.0, budget)
        record: dict[str, Any] = {
            "stage": stage_name,
            "attempt": attempt,
            "provider": provider_id,
            "model": self._model(provider),
            "selection_reason": selection_reason,
            "fallback_usage": fallback_usage,
            "budget_seconds": budget,
        }
        try:
            output = provider.execute(
                TaskInput(task_id=f"aflow-{stage_name}-{attempt}", prompt=prompt)
            )
            try:
                parsed = _drop_empty_optionals(stage_name, _parse_json_object(output.answer))
                candidate, _ = _prune_to_schema(
                    STAGE_OUTPUT_SCHEMAS[stage_name], dict(parsed)
                )
                errors = list(_STAGE_VALIDATORS[stage_name].iter_errors(candidate))
                if errors:
                    raise ValueError(errors[0].message)
            except Exception as exc:
                raise ProviderInvalidResponseError(
                    "Selected provider returned an invalid or non-schema response",
                    internal_detail=f"parse={type(exc).__name__}",
                ) from None
        except ProviderError as exc:
            elapsed = time.monotonic() - started
            record.update(
                elapsed_ms=round(elapsed * 1000),
                outcome=exc.code,
                diagnostic_state="not_valid",
                detail=str(exc)[:500],
            )
            self.attempts.append(record)
            if self.progress:
                self.progress(stage_name, provider_id, attempt, elapsed, max(0.0, budget - elapsed))
            raise
        elapsed = time.monotonic() - started
        record.update(
            elapsed_ms=round(elapsed * 1000),
            outcome="completed",
            diagnostic_state="valid",
        )
        self.attempts.append(record)
        if self.progress:
            self.progress(stage_name, provider_id, attempt, elapsed, max(0.0, budget - elapsed))
        return parsed

    def run_stage(self, stage_name: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        prompt = self._prompt(stage_name, payload)
        try:
            return self._run_attempt(
                self.primary,
                stage_name,
                prompt,
                attempt=1,
                selection_reason="configured_primary",
                fallback_usage=False,
            )
        except _FALLBACK_ELIGIBLE:
            if self.fallback is None or not self.fallback_ready:
                raise
        return self._run_attempt(
            self.fallback,
            stage_name,
            prompt,
            attempt=2,
            selection_reason="single_transient_primary_fallback",
            fallback_usage=True,
        )
