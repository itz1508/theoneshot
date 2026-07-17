"""End-to-end service flow, concurrency, order, and dependency-boundary proof."""

from __future__ import annotations

import importlib
import pkgutil
import sys
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

import audisor
from audisor.api.tasks import get_task_service
from audisor.main import create_app
from provider_testkit import provider_router
from audisor.schemas.task_input import TaskInput, TaskInputBatch
from audisor.schemas.task_output import TaskOutput
from audisor.service import TaskService

RUNTIME_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = RUNTIME_ROOT / "src"


class InstrumentedWorker:
    name = "instrumented"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.active = 0
        self.peak = 0

    def execute(self, task: TaskInput) -> TaskOutput:
        with self._lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            delay, answer = task.prompt.split("|", 1)
            time.sleep(float(delay))
            return TaskOutput(task_id=task.task_id, answer=answer)
        finally:
            with self._lock:
                self.active -= 1


class ReadyWorker:
    name = "ready"

    def execute(self, task: TaskInput) -> TaskOutput:
        assert task.prompt == "Return the word ready."
        return TaskOutput(task_id=task.task_id, answer="ready")


def test_bounded_concurrent_batch_reconstructs_input_order_and_normalizes_answers() -> None:
    worker = InstrumentedWorker()
    service = TaskService(provider_router("fireworks", worker, ReadyWorker()), max_workers=2)
    batch = TaskInputBatch.model_validate(
        [
            {"task_id": "first", "prompt": "0.08|1"},
            {"task_id": "second", "prompt": "0.01|two"},
            {"task_id": "third", "prompt": "0.02|3"},
        ]
    )
    results = service.execute_tasks(batch.root)
    assert [result.task_id for result in results] == ["first", "second", "third"]
    assert [result.answer for result in results] == ["1", "two", "3"]
    assert 1 < worker.peak <= 2


def test_required_ready_api_proof_has_exact_shape_and_task_id() -> None:
    app = create_app()
    app.dependency_overrides[get_task_service] = lambda: TaskService(
        provider_router("fireworks", ReadyWorker(), InstrumentedWorker()), max_workers=1
    )
    response = TestClient(app).post(
        "/v1/tasks",
        json=[{"task_id": "task-001", "prompt": "Return the word ready."}],
    )
    assert response.status_code == 200
    assert response.json() == [{"task_id": "task-001", "answer": "ready"}]


def test_all_source_modules_compile_and_import_from_this_runtime() -> None:
    for source_path in SOURCE_ROOT.rglob("*.py"):
        compile(source_path.read_text(encoding="utf-8"), str(source_path), "exec")

    imported = [audisor]
    for module in pkgutil.walk_packages(audisor.__path__, prefix="audisor."):
        imported.append(importlib.import_module(module.name))

    source_root = SOURCE_ROOT.resolve()
    for module in imported:
        module_path = getattr(module, "__file__", None)
        if module_path:
            Path(module_path).resolve().relative_to(source_root)


def test_runtime_has_no_amd_source_package_or_path_dependency() -> None:
    inspected = [RUNTIME_ROOT / "pyproject.toml"]
    lock_path = RUNTIME_ROOT / "uv.lock"
    if lock_path.exists():
        inspected.append(lock_path)
    forbidden = (
        "amd_track1",
        "edge_backend",
        "audisor_backend",
        "hackaton-uipath",
        "d:/dev/amd",
        "d:\\dev\\amd",
        "d:/dev/edge",
        "d:\\dev\\edge",
        "d:/dev/hackaton-uipath-jun29-workbench",
        "d:\\dev\\hackaton-uipath-jun29-workbench",
        "d:/dev/theoneshot/audisor",
        "d:\\dev\\theoneshot\\audisor",
        "file://",
    )
    for path in inspected:
        text = path.read_text(encoding="utf-8").lower()
        assert not any(token in text for token in forbidden), f"forbidden dependency token in {path}"

    assert not any(name == "amd_track1" or name.startswith("amd_track1.") for name in sys.modules)
    for module in list(sys.modules.values()):
        module_path = getattr(module, "__file__", None)
        if module_path:
            normalized = str(Path(module_path).resolve()).replace("\\", "/").lower()
            assert not any(
                reference_path in normalized
                for reference_path in (
                    "/dev/amd/",
                    "/dev/edge/",
                    "/dev/hackaton-uipath-jun29-workbench/",
                    "/dev/theoneshot/audisor/",
                )
            )
