"""Secure Phase 2B end-to-end execution without requiring live Docker."""

import json
import shutil
from pathlib import Path

from audisor.builder.authority import TargetAuthorityResolver, capture_tree
from audisor.builder.execution_store import ExecutionStore
from audisor.builder.executor import BuildExecutor
from audisor.builder.skill_renderer import render_skills
from audisor.builder.store import BuildStore
from audisor.builder.task_loader import PreparedBuildLoader
from provider_testkit import provider_router
from audisor.schemas.build import BuildPlan, BuildRequest
from audisor.schemas.execution import BuildExecutionRequest
from audisor.schemas.task_input import TaskInput
from audisor.audisor_lifecycle.operation import FrozenAudisorPolicy
from audisor.audisor_lifecycle.ignition import IgnitionResult


class QueueWorker:
    name = "fake-execution"

    def __init__(self, answers: list[object]) -> None:
        self.answers = list(answers)
        self.calls: list[TaskInput] = []

    def execute(self, task: TaskInput) -> object:
        self.calls.append(task)
        if not self.answers:
            raise AssertionError("unexpected worker call")
        return self.answers.pop(0)


class NeverWorker:
    name = "never"

    def execute(self, task: TaskInput) -> object:
        raise AssertionError(f"fallback worker called for {task.task_id}")


def task_prompt(label: str) -> str:
    return f"""## Objective
Create {label}.

## Inputs and repository paths
Use only the isolated workspace.

## Required work
Create {label}.

## Ordered steps
1. Create the requested file.

## Expected output
The requested file exists.

## Validation
Record prepared executable validation as deferred and perform static verification.

## Evidence to return
Return changed paths and hashes."""


def prepare(data: Path, build_id: str = "builder-proof-001") -> BuildStore:
    store = BuildStore(data)
    tasks = [
        {
            "task_id": "task-001",
            "title": "Create greeting module",
            "depends_on": [],
            "prompt": task_prompt("src/greeting.py"),
            "expected_outputs": ["src/greeting.py"],
            "validation": [{"argv": ["python", "-m", "pytest", "tests/test_greeting.py", "-q"], "working_directory": ".", "acceptable_exit_codes": [0], "timeout_seconds": 120}],
        },
        {
            "task_id": "task-002",
            "title": "Create greeting tests",
            "depends_on": ["task-001"],
            "prompt": task_prompt("tests/test_greeting.py"),
            "expected_outputs": ["tests/test_greeting.py"],
            "validation": [{"argv": ["python", "-m", "pytest", "tests/test_greeting.py", "-q"], "working_directory": ".", "acceptable_exit_codes": [0], "timeout_seconds": 120}],
        },
        {
            "task_id": "task-003",
            "title": "Create usage documentation",
            "depends_on": [],
            "prompt": task_prompt("docs/usage.md"),
            "expected_outputs": ["docs/usage.md"],
            "validation": [{"argv": ["python", "-c", "print('static')"], "working_directory": ".", "acceptable_exit_codes": [0], "timeout_seconds": 30}],
        },
    ]
    plan = BuildPlan.model_validate(
        {"build_id": build_id, "status": "ready", "gaps": [], "tasks": tasks}
    )
    store.publish(
        BuildRequest(build_id=build_id, instruction="Create greeting and docs."),
        plan,
        render_skills(build_id, plan.tasks),
    )
    return store


def answer(path: str, content: str) -> str:
    return json.dumps(
        {
            "summary": f"Create {path}.",
            "mutations": [
                {"action_id": "mutation-001", "type": "write_file", "path": path, "content": content}
            ],
            "expected_changed_paths": [path],
        }
    )


def success_answers() -> list[str]:
    return [
        answer("src/greeting.py", "def greet(name):\n    return f'Hello, {name}'\n"),
        answer("tests/test_greeting.py", "def test_greet():\n    assert True\n"),
        answer("docs/usage.md", "# Usage\n"),
    ]


def target(tmp_path: Path) -> Path:
    root = tmp_path / "target-project"
    for name in ("src", "tests", "docs"):
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


def request(root: Path) -> BuildExecutionRequest:
    return BuildExecutionRequest(
        execution_id="execution-001",
        idempotency_key="execution-001-request",
        target_root=str(root),
        allowed_write_paths=["src", "tests", "docs"],
    )


def make_executor(tmp_path: Path, worker: QueueWorker, *, enabled: bool = False, aflow_igniter=None, aflow_worker_factory=None, events: list | None = None) -> tuple[BuildExecutor, BuildStore]:
    store = prepare(tmp_path / "data")
    execution_store = ExecutionStore(data_dir=store.data_dir)
    return (
        BuildExecutor(
            router=provider_router("fireworks", worker, NeverWorker()),
            loader=PreparedBuildLoader(store),
            authority=TargetAuthorityResolver(
                data_dir=store.data_dir,
                product_root=tmp_path / "product-source",
                reference_roots=(tmp_path / "reference",),
                approved_target_roots=(tmp_path,),
            ),
            store=execution_store,
            aflow_policy_reader=lambda: (events.append("policy") if events is not None else None) or FrozenAudisorPolicy(enabled, "local-openai-compatible", "qwen2.5-coder:7b", "http://127.0.0.1:11434"),
            aflow_igniter=aflow_igniter,
            aflow_worker_factory=aflow_worker_factory or (lambda *args, **kwargs: object()),
        ),
        store,
    )


def test_enabled_build_invokes_once_persists_before_implementation_and_replays(tmp_path: Path) -> None:
    events: list = []
    aflow_calls: list = []

    def aflow(operation_context, policy, worker):
        events.append("ignite")
        aflow_calls.append((operation_context, policy, worker))
        return IgnitionResult(True, "supplied", operation_context.accepted_plan, {"readiness": {"aflow_decision": "no_material_gap"}}, True)

    worker = QueueWorker(success_answers())
    original_execute = worker.execute
    def execute(task):
        events.append("implementation")
        return original_execute(task)
    worker.execute = execute
    executor, store = make_executor(tmp_path, worker, enabled=True, aflow_igniter=aflow, events=events)
    original_persist = executor.store.persist_audisor_result
    def persist(path, artifact):
        events.append("persist")
        return original_persist(path, artifact)
    executor.store.persist_audisor_result = persist
    first = executor.execute("builder-proof-001", request(target(tmp_path)))
    second = executor.execute("builder-proof-001", request(target(tmp_path)))
    assert first.status == second.status == "completed"
    assert len(aflow_calls) == 1
    assert events.index("persist") < events.index("implementation")
    assert events.count("policy") == 1
    assert (store.build_path("builder-proof-001") / "executions" / "execution-001" / "evidence" / "aflow-operation-result.json").is_file()


def test_audisor_rejection_terminalizes_releases_authority_and_cleans_workspace(tmp_path: Path) -> None:
    events: list = []
    def reject(**kwargs):
        events.append("ignite")
        return IgnitionResult(True, "supplied", kwargs["operation_context"].accepted_plan, {"readiness": {}}, False)
    worker = QueueWorker(success_answers())
    executor, store = make_executor(tmp_path, worker, enabled=True, aflow_igniter=reject, events=events)
    state = executor.execute("builder-proof-001", request(target(tmp_path)))
    execution = store.build_path("builder-proof-001") / "executions" / "execution-001"
    assert state.status == "failed"
    assert events == ["policy", "ignite"]
    assert worker.calls == []
    assert (execution / "evidence" / "aflow-operation-result.json").is_file()
    assert not (execution / "workspace").exists()


def test_three_task_success_is_manifest_bound_and_target_unchanged(tmp_path: Path) -> None:
    root = target(tmp_path)
    before = capture_tree(root)
    worker = QueueWorker(success_answers())
    executor, store = make_executor(tmp_path, worker)
    state = executor.execute("builder-proof-001", request(root))
    assert state.status == "completed"
    assert [item.status for item in state.tasks] == ["completed"] * 3
    assert state.terminal_manifest_sha256
    assert [task.task_id for task in worker.calls] == ["task-001", "task-002", "task-003"]
    assert all("Fully resolved allowed workspace write roots" in task.prompt for task in worker.calls)
    assert capture_tree(root) == before
    execution = store.build_path("builder-proof-001") / "executions/execution-001"
    assert (execution / "terminal-manifest.json").is_file()
    manifest = json.loads((execution / "terminal-manifest.json").read_text())
    manifest_paths = {artifact["path"] for artifact in manifest["artifacts"]}
    assert {
        "global-authority/claim.json",
        "global-authority/release-evidence.json",
        "prepared-plan.json",
    } <= manifest_paths
    release_history = list(
        (store.data_dir / "authority-locks/history").glob("*/*.released.json")
    )
    assert len(release_history) == 1
    release = json.loads(release_history[0].read_text(encoding="utf-8"))
    assert release["terminal_manifest_sha256"] == state.terminal_manifest_sha256
    assert release["reconciliation_verified"] is True
    assert (execution / "workspace/src/greeting.py").is_file()
    for task_id in ("task-001", "task-002", "task-003"):
        result = json.loads((execution / f"results/{task_id}.json").read_text())
        assert result["worker_dispatched"] is True
        assert result["prepared_validation_sha256"] == result["rendered_validation_sha256"]
        assert result["executed_validation_sha256"] is None
        assert result["validation_commands"] == []
        assert result["exit_codes"] == []


def test_failure_blocks_dependents_but_runs_independent_branch(tmp_path: Path) -> None:
    root = target(tmp_path)
    worker = QueueWorker(
        [
            json.dumps(
                {
                    "summary": "Delete a missing required output.",
                    "mutations": [
                        {
                            "action_id": "mutation-001",
                            "type": "delete_file",
                            "path": "src/greeting.py",
                        }
                    ],
                    "expected_changed_paths": ["src/greeting.py"],
                }
            ),
            answer("docs/usage.md", "# Usage\n"),
        ]
    )
    executor, store = make_executor(tmp_path, worker)
    state = executor.execute("builder-proof-001", request(root))
    assert state.status == "failed"
    assert [item.status for item in state.tasks] == ["failed", "blocked", "completed"]
    assert [task.task_id for task in worker.calls] == ["task-001", "task-003"]
    blocked = json.loads(
        (store.build_path("builder-proof-001") / "executions/execution-001/results/task-002.json").read_text()
    )
    assert blocked["worker_dispatched"] is False
    assert state.terminal_manifest_sha256


def test_idempotent_replay_survives_removed_target_without_worker_call(tmp_path: Path) -> None:
    root = target(tmp_path)
    worker = QueueWorker(success_answers())
    executor, _store = make_executor(tmp_path, worker)
    first = executor.execute("builder-proof-001", request(root))
    shutil.rmtree(root)
    second = executor.execute("builder-proof-001", request(root))
    assert second == first
    assert len(worker.calls) == 3


def test_executable_validation_is_durably_deferred(tmp_path: Path) -> None:
    root = target(tmp_path)
    worker = QueueWorker(success_answers())
    executor, store = make_executor(tmp_path, worker)
    state = executor.execute("builder-proof-001", request(root))
    assert state.status == "completed"
    execution = store.build_path("builder-proof-001") / "executions/execution-001"
    for task_id in ("task-001", "task-002", "task-003"):
        result = json.loads((execution / f"results/{task_id}.json").read_text())
        assert result["validation_commands"] == []
        assert result["exit_codes"] == []
        assert result["executed_validation_sha256"] is None


def test_worker_plan_cannot_author_commands(tmp_path: Path) -> None:
    root = target(tmp_path)
    worker = QueueWorker(
        [json.dumps({"summary": "bad", "mutations": [{"action_id": "x", "type": "run_command", "argv": ["python", "x.py"]}], "expected_changed_paths": ["src/x.py"]}), success_answers()[2]]
    )
    executor, _store = make_executor(tmp_path, worker)
    state = executor.execute("builder-proof-001", request(root))
    assert state.status == "failed"
    assert state.tasks[0].status == "failed"
    assert state.tasks[1].status == "blocked"


def test_lone_surrogate_becomes_durable_failure_evidence(tmp_path: Path) -> None:
    root = target(tmp_path)
    worker = QueueWorker(["\ud800", success_answers()[2]])
    executor, store = make_executor(tmp_path, worker)
    state = executor.execute("builder-proof-001", request(root))
    assert state.status == "failed"
    result = store.build_path("builder-proof-001") / "executions/execution-001/results/task-001.json"
    assert "\\ud800" in result.read_text(encoding="utf-8")
