import json

from audisor.file_runner import run_file_tasks
from audisor.schemas.task_output import TaskOutput


class StubService:
    def __init__(self):
        self.tasks = None

    def execute_tasks(self, tasks):
        self.tasks = tasks
        return [TaskOutput(task_id=task.task_id, answer=f"answer for {task.task_id}") for task in tasks]


def test_file_runner_reads_task_array_and_writes_matching_result_array(tmp_path):
    input_path = tmp_path / "input" / "tasks.json"
    output_path = tmp_path / "output" / "results.json"
    input_path.parent.mkdir()
    input_path.write_text(
        json.dumps([
            {"task_id": "a1", "prompt": "Explain an API."},
            {"task_id": "a2", "prompt": "What is 20 percent of 150?"},
        ]),
        encoding="utf-8",
    )
    service = StubService()

    run_file_tasks(service=service, input_path=input_path, output_path=output_path)

    assert [task.task_id for task in service.tasks] == ["a1", "a2"]
    assert json.loads(output_path.read_text(encoding="utf-8")) == [
        {"task_id": "a1", "answer": "answer for a1"},
        {"task_id": "a2", "answer": "answer for a2"},
    ]


def test_file_runner_rejects_duplicate_ids_before_service_call(tmp_path):
    input_path = tmp_path / "tasks.json"
    input_path.write_text(
        json.dumps([
            {"task_id": "a1", "prompt": "first"},
            {"task_id": "a1", "prompt": "duplicate"},
        ]),
        encoding="utf-8",
    )
    service = StubService()

    try:
        run_file_tasks(service=service, input_path=input_path, output_path=tmp_path / "results.json")
    except ValueError as exc:
        assert "duplicate" in str(exc)
    else:
        raise AssertionError("duplicate task IDs must be rejected")
    assert service.tasks is None
