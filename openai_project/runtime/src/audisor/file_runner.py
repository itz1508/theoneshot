"""Run Audisor tasks through the JSON file contract."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Protocol

from audisor.schemas.task_input import TaskInputBatch
from audisor.schemas.task_output import TaskOutput


class TaskBatchService(Protocol):
    def execute_tasks(self, tasks) -> list[TaskOutput]: ...


def _write_results(path: Path, results: list[TaskOutput]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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


def run_file_tasks(
    *,
    service: TaskBatchService,
    input_path: str | Path = "/input/tasks.json",
    output_path: str | Path = "/output/results.json",
) -> list[TaskOutput]:
    """Read, validate, execute, and atomically write one task batch."""

    source = Path(input_path)
    target = Path(output_path)
    with source.open("r", encoding="utf-8") as stream:
        batch = TaskInputBatch.model_validate_json(stream.read())
    results = service.execute_tasks(batch.root)
    if len(results) != len(batch.root) or [item.task_id for item in results] != [item.task_id for item in batch.root]:
        raise ValueError("service returned results that do not match the input batch")
    _write_results(target, results)
    return results
