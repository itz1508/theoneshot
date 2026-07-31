"""Provider-routed A-Flow stage workers with bounded fallback evidence."""

from __future__ import annotations

import json
import hashlib
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from audisor.schemas.task_input import TaskInput
from audisor.workers.base import (
    ProviderError,
    ProviderContractInvalidError,
    ProviderCapabilityError,
    ProviderInvalidResponseError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderResponseNotJsonError,
    ProviderSchemaUnsupportedError,
    WorkerProvider,
)
from audisor.workers.local import LocalWorker

from .operation import FrozenAudisorPolicy
from .output_processing import _drop_empty_optionals, _parse_json_object, _prune_to_schema
from .stage_contracts import STAGE_OUTPUT_SCHEMAS, StageOutputError, _STAGE_VALIDATORS
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
                "Return only the JSON value. The complete canonical JSON Schema is:",
                json.dumps(STAGE_OUTPUT_SCHEMAS[stage_name], ensure_ascii=False, sort_keys=True),
                "A shape example is:",
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

_UNSUPPORTED_NATIVE_SCHEMA_KEYS = frozenset(
    {"$ref", "$defs", "definitions", "oneOf", "anyOf", "allOf", "not", "if", "then", "else", "patternProperties", "dependentSchemas"}
)


def _schema_supports_native_mode(schema: Any) -> bool:
    if isinstance(schema, Mapping):
        if any(key in schema for key in _UNSUPPORTED_NATIVE_SCHEMA_KEYS):
            return False
        return all(_schema_supports_native_mode(value) for value in schema.values())
    if isinstance(schema, list):
        return all(_schema_supports_native_mode(value) for value in schema)
    return True


def _select_schema_mode(provider: WorkerProvider, schema: Mapping[str, Any]) -> str:
    capabilities = provider.capabilities()
    if capabilities.native_json_schema and _schema_supports_native_mode(schema):
        return "native_json_schema"
    return "prompt_validated_json"


@dataclass
class ManagedStageWorker:
    """Run a stage on the primary provider and at most one ready fallback."""

    primary: WorkerProvider
    fallback: WorkerProvider | None = None
    fallback_ready: bool = False
    progress: Callable[[str, str, int, float, float], None] | None = None
    invalidate_readiness: Callable[[WorkerProvider, ProviderError, str], None] | None = None

    def __post_init__(self) -> None:
        self.attempts: list[dict[str, Any]] = []

    @staticmethod
    def _prompt(stage_name: str, payload: Mapping[str, Any]) -> str:
        return "\n\n".join(
            (
                _STAGE_INSTRUCTIONS[stage_name],
                "Return only the JSON value. The complete canonical JSON Schema is:",
                json.dumps(STAGE_OUTPUT_SCHEMAS[stage_name], ensure_ascii=False, sort_keys=True),
                "A shape example is:",
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
        deadline: float | None = None,
    ) -> Mapping[str, Any]:
        started = time.monotonic()
        provider_id = provider.provider_id
        provider_budget = float(getattr(provider, "timeout_seconds", 0.0) or 0.0)
        # Clamp budget to the remaining stage deadline to prevent overlap.
        if deadline is not None:
            remaining = max(0.0, deadline - started)
            budget = min(provider_budget, remaining) if provider_budget > 0 else remaining
        else:
            budget = provider_budget
        # Enforce the deadline contract: if no time remains, fail fast.
        if deadline is not None and budget <= 0:
            record: dict[str, Any] = {
                "stage": stage_name,
                "attempt": attempt,
                "provider": provider_id,
                "model": self._model(provider),
                "selection_reason": selection_reason,
                "fallback_usage": fallback_usage,
                "budget_seconds": 0.0,
                "elapsed_ms": 0,
                "outcome": "provider_timeout",
                "diagnostic_state": "not_valid",
                "detail": "stage deadline exhausted before attempt started",
            }
            self.attempts.append(record)
            raise ProviderTimeoutError(
                f"stage deadline exhausted before attempt {attempt}",
                internal_detail=f"provider={provider_id}",
            )
        # Apply the clamped budget to the provider so its internal timeout matches.
        if hasattr(provider, "timeout_seconds"):
            provider.timeout_seconds = budget
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
            task = TaskInput(task_id=f"aflow-{stage_name}-{attempt}", prompt=prompt)
            schema_mode = _select_schema_mode(provider, STAGE_OUTPUT_SCHEMAS[stage_name])
            execute_structured = getattr(provider, "execute_structured", None)
            if callable(execute_structured):
                try:
                    output = execute_structured(
                        task,
                        schema=STAGE_OUTPUT_SCHEMAS[stage_name],
                        schema_name=f"aflow_{stage_name}",
                        schema_mode=schema_mode,
                    )
                except (ProviderCapabilityError, ProviderSchemaUnsupportedError):
                    if schema_mode != "native_json_schema":
                        raise
                    schema_mode = "prompt_validated_json"
                    output = execute_structured(
                        task,
                        schema=STAGE_OUTPUT_SCHEMAS[stage_name],
                        schema_name=f"aflow_{stage_name}",
                        schema_mode=schema_mode,
                    )
            else:
                output = provider.execute(task)
            answer_bytes = output.answer.encode("utf-8")
            schema_digest = hashlib.sha256(
                json.dumps(STAGE_OUTPUT_SCHEMAS[stage_name], sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            try:
                parsed = _drop_empty_optionals(stage_name, _parse_json_object(output.answer))
                candidate, _ = _prune_to_schema(
                    STAGE_OUTPUT_SCHEMAS[stage_name], dict(parsed)
                )
                errors = list(_STAGE_VALIDATORS[stage_name].iter_errors(candidate))
                if errors:
                    first = errors[0]
                    detail = {
                        "stage": stage_name,
                        "json_path": first.json_path,
                        "validator_keyword": str(first.validator),
                        "validator_message": first.message[:300],
                        "provider_id": provider_id,
                        "model_id": self._model(provider),
                        "attempt_id": attempt,
                        "schema_mode": schema_mode,
                        "schema_digest": schema_digest,
                        "response_digest": hashlib.sha256(answer_bytes).hexdigest(),
                        "response_byte_count": len(answer_bytes),
                    }
                    raise ProviderContractInvalidError(
                        "Selected provider response violated the canonical stage contract",
                        internal_detail=json.dumps(detail, sort_keys=True),
                    )
            except StageOutputError:
                raise ProviderResponseNotJsonError(
                    "Selected provider response was not a JSON object",
                    internal_detail=json.dumps(
                        {
                            "stage": stage_name,
                            "provider_id": provider_id,
                            "model_id": self._model(provider),
                            "attempt_id": attempt,
                            "schema_mode": schema_mode,
                            "schema_digest": schema_digest,
                            "response_digest": hashlib.sha256(answer_bytes).hexdigest(),
                            "response_byte_count": len(answer_bytes),
                        },
                        sort_keys=True,
                    ),
                ) from None
            except ProviderError:
                raise
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
            if self.invalidate_readiness and not (
                isinstance(exc, ProviderContractInvalidError)
                and schema_mode == "prompt_validated_json"
            ):
                self.invalidate_readiness(provider, exc, schema_mode)
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

    def run_stage(
        self, stage_name: str, payload: Mapping[str, Any],
        *, stage_deadline: float | None = None,
    ) -> Mapping[str, Any]:
        prompt = self._prompt(stage_name, payload)
        try:
            return self._run_attempt(
                self.primary,
                stage_name,
                prompt,
                attempt=1,
                selection_reason="configured_primary",
                fallback_usage=False,
                deadline=stage_deadline,
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
            deadline=stage_deadline,
        )
