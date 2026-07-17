"""Runtime and published JSON schema coverage."""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from audisor.schemas.task_input import TaskInputBatch
from audisor.schemas.task_output import TaskOutput

SCHEMA_ROOT = Path(__file__).resolve().parents[2] / "schemas"


def load_schema(name: str) -> dict:
    return json.loads((SCHEMA_ROOT / name).read_text(encoding="utf-8"))


def test_valid_single_and_batch_preserve_original_values_and_ignore_extras() -> None:
    batch = TaskInputBatch.model_validate(
        [
            {"task_id": " task-001 ", "prompt": "  complete prompt\n", "ignored": True},
            {"task_id": "task-002", "prompt": "second"},
        ]
    )
    assert batch.root[0].task_id == " task-001 "
    assert batch.root[0].prompt == "  complete prompt\n"
    assert batch.root[0].model_dump() == {
        "task_id": " task-001 ",
        "prompt": "  complete prompt\n",
    }


@pytest.mark.parametrize(
    "payload",
    [
        [],
        [{"task_id": "", "prompt": "x"}],
        [{"task_id": "   ", "prompt": "x"}],
        [{"task_id": "x", "prompt": ""}],
        [{"task_id": "x", "prompt": "\n\t"}],
        [{"task_id": 1, "prompt": "x"}],
        [{"task_id": "x", "prompt": 1}],
        [
            {"task_id": "duplicate", "prompt": "one"},
            {"task_id": "duplicate", "prompt": "two"},
        ],
    ],
)
def test_invalid_batches_are_rejected(payload: object) -> None:
    with pytest.raises(ValidationError):
        TaskInputBatch.model_validate(payload)


def test_duplicate_check_is_exact_and_does_not_trim_ids() -> None:
    batch = TaskInputBatch.model_validate(
        [
            {"task_id": "same", "prompt": "one"},
            {"task_id": " same ", "prompt": "two"},
        ]
    )
    assert [task.task_id for task in batch.root] == ["same", " same "]


def test_published_task_schemas_are_valid_and_match_examples() -> None:
    input_schema = load_schema("task-input.schema.json")
    output_schema = load_schema("task-output.schema.json")
    Draft202012Validator.check_schema(input_schema)
    Draft202012Validator.check_schema(output_schema)
    Draft202012Validator(input_schema).validate(
        [{"task_id": "task-001", "prompt": "Return ready.", "ignored": "value"}]
    )
    payload = [TaskOutput(task_id="task-001", answer="ready").model_dump()]
    Draft202012Validator(output_schema).validate(payload)
