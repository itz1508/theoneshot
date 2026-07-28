"""Local stage worker: one stage per call over the OpenAI-compatible endpoint."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from audisor.schemas.task_input import TaskInput
from audisor.workers.local import LocalWorker

from .operation import FrozenAudisorPolicy
from .output_processing import _drop_empty_optionals, _parse_json_object
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
