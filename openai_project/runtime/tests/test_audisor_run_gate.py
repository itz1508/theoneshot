from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from audisor import cli
from audisor.audisor_lifecycle.ignition import IgnitionResult
from audisor.audisor_lifecycle.operation import FrozenAudisorPolicy
from audisor.schemas.task_output import TaskOutput


def _write_tasks(path: Path, tasks: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(tasks), encoding="utf-8")


class CountingService:
    def __init__(self):
        self.calls = 0
        self.tasks = None

    def execute_tasks(self, tasks):
        self.calls += 1
        self.tasks = tasks
        return [TaskOutput(task_id=task.task_id, answer=f"answer for {task.task_id}") for task in tasks]


def test_run_aflow_off_skips_ignite_and_runs_provider(tmp_path, monkeypatch):
    input_path = tmp_path / "input" / "tasks.json"
    output_path = tmp_path / "output" / "results.json"
    _write_tasks(input_path, [{"task_id": "a1", "prompt": "Explain an API."}])

    monkeypatch.setenv("AUDISOR_CONFIG_PATH", str(tmp_path / "config.json"))
    assert cli.main(["aflow", "off"]) == 0

    service = CountingService()

    def mock_run_file_tasks(*, service, input_path, output_path):
        from audisor.schemas.task_input import TaskInputBatch
        with open(input_path, "r", encoding="utf-8") as f:
            batch = TaskInputBatch.model_validate_json(f.read())
        results = service.execute_tasks(batch.root)
        import json, os, tempfile
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps([r.model_dump() for r in results], ensure_ascii=False, indent=2) + "\n"
        fd, temporary = tempfile.mkstemp(prefix=f".{output_path.name}.", suffix=".tmp", dir=output_path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, output_path)
        except BaseException:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise

    monkeypatch.setattr(cli, "run_file_tasks", mock_run_file_tasks)
    monkeypatch.setattr(cli, "TaskService", lambda router: service)
    monkeypatch.setattr(cli, "get_provider_router", lambda: object())

    assert cli.main(["run", "--input", str(input_path), "--output", str(output_path)]) == 0
    assert service.calls == 1
    assert json.loads(output_path.read_text(encoding="utf-8")) == [{"task_id": "a1", "answer": "answer for a1"}]


def test_run_aflow_on_accepted_continues_to_provider(tmp_path, monkeypatch):
    input_path = tmp_path / "input" / "tasks.json"
    output_path = tmp_path / "output" / "results.json"
    _write_tasks(input_path, [{"task_id": "a2", "prompt": "What is 20 percent of 150?"}])

    monkeypatch.setenv("AUDISOR_CONFIG_PATH", str(tmp_path / "config.json"))
    assert cli.main(["aflow", "on"]) == 0

    ignite_calls = []

    def fake_ignite(*, policy, **kwargs):
        ignite_calls.append(policy)
        return IgnitionResult(
            lifecycle_selected=True,
            candidate_plan_source=None,
            candidate_plan=None,
            execution_contract=None,
            implementation_eligible=True,
        )

    monkeypatch.setattr("audisor.audisor_run_gate.ignite", fake_ignite)

    service = CountingService()

    def mock_run_file_tasks(*, service, input_path, output_path):
        from audisor.schemas.task_input import TaskInputBatch
        with open(input_path, "r", encoding="utf-8") as f:
            batch = TaskInputBatch.model_validate_json(f.read())
        results = service.execute_tasks(batch.root)
        import json, os, tempfile
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps([r.model_dump() for r in results], ensure_ascii=False, indent=2) + "\n"
        fd, temporary = tempfile.mkstemp(prefix=f".{output_path.name}.", suffix=".tmp", dir=output_path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, output_path)
        except BaseException:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise

    monkeypatch.setattr(cli, "run_file_tasks", mock_run_file_tasks)
    monkeypatch.setattr(cli, "TaskService", lambda router: service)
    monkeypatch.setattr(cli, "get_provider_router", lambda: object())

    assert cli.main(["run", "--input", str(input_path), "--output", str(output_path)]) == 0
    assert len(ignite_calls) == 1
    assert service.calls == 1
    assert json.loads(output_path.read_text(encoding="utf-8")) == [{"task_id": "a2", "answer": "answer for a2"}]


def test_run_aflow_on_rejected_writes_failure_results(tmp_path, monkeypatch):
    input_path = tmp_path / "input" / "tasks.json"
    output_path = tmp_path / "output" / "results.json"
    _write_tasks(input_path, [{"task_id": "a3", "prompt": "Summarize this."}])

    monkeypatch.setenv("AUDISOR_CONFIG_PATH", str(tmp_path / "config.json"))
    assert cli.main(["aflow", "on"]) == 0

    ignite_calls = []

    def fake_ignite(*, policy, **kwargs):
        ignite_calls.append(policy)
        return IgnitionResult(
            lifecycle_selected=True,
            candidate_plan_source=None,
            candidate_plan=None,
            execution_contract=None,
            implementation_eligible=False,
        )

    monkeypatch.setattr("audisor.audisor_run_gate.ignite", fake_ignite)

    service = CountingService()
    monkeypatch.setattr(cli, "run_file_tasks", lambda *, service, input_path, output_path: service.execute_tasks(service.tasks or []))
    monkeypatch.setattr(cli, "TaskService", lambda router: service)
    monkeypatch.setattr(cli, "get_provider_router", lambda: object())

    stderr = io.StringIO()
    assert cli.main(["run", "--input", str(input_path), "--output", str(output_path)], stderr=stderr) == 1
    assert len(ignite_calls) == 1
    assert service.calls == 0
    assert "AFlowRejected" in stderr.getvalue()

    results = json.loads(output_path.read_text(encoding="utf-8"))
    assert len(results) == 1
    assert results[0]["task_id"] == "a3"
    assert "Audisor rejected" in results[0]["answer"]


def test_run_aflow_exception_writes_failure_results_safely(tmp_path, monkeypatch):
    input_path = tmp_path / "input" / "tasks.json"
    output_path = tmp_path / "output" / "results.json"
    _write_tasks(input_path, [{"task_id": "a4", "prompt": "Debug this code."}])

    monkeypatch.setenv("AUDISOR_CONFIG_PATH", str(tmp_path / "config.json"))
    assert cli.main(["aflow", "on"]) == 0

    ignite_calls = []

    def fake_ignite(*, policy, **kwargs):
        ignite_calls.append(policy)
        raise RuntimeError("simulated Audisor failure")

    monkeypatch.setattr("audisor.audisor_run_gate.ignite", fake_ignite)

    service = CountingService()
    monkeypatch.setattr(cli, "run_file_tasks", lambda *, service, input_path, output_path: service.execute_tasks(service.tasks or []))
    monkeypatch.setattr(cli, "TaskService", lambda router: service)
    monkeypatch.setattr(cli, "get_provider_router", lambda: object())

    stderr = io.StringIO()
    assert cli.main(["run", "--input", str(input_path), "--output", str(output_path)], stderr=stderr) == 1
    assert len(ignite_calls) == 1
    assert service.calls == 0
    assert "AFlowRejected" in stderr.getvalue()

    results = json.loads(output_path.read_text(encoding="utf-8"))
    assert len(results) == 1
    assert results[0]["task_id"] == "a4"
    assert "Audisor rejected" in results[0]["answer"]


def test_run_no_duplicate_ignite_calls_for_multiple_tasks(tmp_path, monkeypatch):
    input_path = tmp_path / "input" / "tasks.json"
    output_path = tmp_path / "output" / "results.json"
    _write_tasks(input_path, [
        {"task_id": "b1", "prompt": "First task."},
        {"task_id": "b2", "prompt": "Second task."},
    ])

    monkeypatch.setenv("AUDISOR_CONFIG_PATH", str(tmp_path / "config.json"))
    assert cli.main(["aflow", "on"]) == 0

    ignite_calls = []

    def fake_ignite(*, policy, **kwargs):
        ignite_calls.append(policy)
        return IgnitionResult(
            lifecycle_selected=True,
            candidate_plan_source=None,
            candidate_plan=None,
            execution_contract=None,
            implementation_eligible=True,
        )

    monkeypatch.setattr("audisor.audisor_run_gate.ignite", fake_ignite)

    service = CountingService()

    def mock_run_file_tasks(*, service, input_path, output_path):
        from audisor.schemas.task_input import TaskInputBatch
        with open(input_path, "r", encoding="utf-8") as f:
            batch = TaskInputBatch.model_validate_json(f.read())
        results = service.execute_tasks(batch.root)
        import json, os, tempfile
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps([r.model_dump() for r in results], ensure_ascii=False, indent=2) + "\n"
        fd, temporary = tempfile.mkstemp(prefix=f".{output_path.name}.", suffix=".tmp", dir=output_path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, output_path)
        except BaseException:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise

    monkeypatch.setattr(cli, "run_file_tasks", mock_run_file_tasks)
    monkeypatch.setattr(cli, "TaskService", lambda router: service)
    monkeypatch.setattr(cli, "get_provider_router", lambda: object())

    assert cli.main(["run", "--input", str(input_path), "--output", str(output_path)]) == 0
    assert len(ignite_calls) == 1
    assert service.calls == 1


def test_run_aflow_uses_configured_provider_not_hardcoded_local(tmp_path, monkeypatch):
    input_path = tmp_path / "input" / "tasks.json"
    output_path = tmp_path / "output" / "results.json"
    _write_tasks(input_path, [{"task_id": "c1", "prompt": "Classify sentiment."}])

    monkeypatch.setenv("AUDISOR_CONFIG_PATH", str(tmp_path / "config.json"))
    assert cli.main(["aflow", "on"]) == 0

    captured_policy = []

    def fake_ignite(*, policy, **kwargs):
        captured_policy.append(policy)
        return IgnitionResult(
            lifecycle_selected=True,
            candidate_plan_source=None,
            candidate_plan=None,
            execution_contract=None,
            implementation_eligible=True,
        )

    monkeypatch.setattr("audisor.audisor_run_gate.ignite", fake_ignite)

    service = CountingService()

    def mock_run_file_tasks(*, service, input_path, output_path):
        from audisor.schemas.task_input import TaskInputBatch
        with open(input_path, "r", encoding="utf-8") as f:
            batch = TaskInputBatch.model_validate_json(f.read())
        results = service.execute_tasks(batch.root)
        import json, os, tempfile
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps([r.model_dump() for r in results], ensure_ascii=False, indent=2) + "\n"
        fd, temporary = tempfile.mkstemp(prefix=f".{output_path.name}.", suffix=".tmp", dir=output_path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, output_path)
        except BaseException:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise

    monkeypatch.setattr(cli, "run_file_tasks", mock_run_file_tasks)
    monkeypatch.setattr(cli, "TaskService", lambda router: service)
    monkeypatch.setattr(cli, "get_provider_router", lambda: object())

    assert cli.main(["run", "--input", str(input_path), "--output", str(output_path)]) == 0
    assert len(captured_policy) == 1
    # The policy should come from read_frozen_audisor_policy, not a hardcoded local-only default
    assert isinstance(captured_policy[0], FrozenAudisorPolicy)
    assert captured_policy[0].enabled is True