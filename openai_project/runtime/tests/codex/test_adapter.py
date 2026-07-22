from __future__ import annotations

import hashlib
import uuid
from types import SimpleNamespace

import pytest
from dulwich import porcelain

from audisor.codex.adapter import CodexAdapter, CodexAdapterError
from audisor.codex.models import CodexRunResult
from audisor.codex.task_entry import prepare_and_run_task
from audisor import cli
from audisor.builder.skill_renderer import render_skills
from audisor.builder.store import BuildStore
from audisor.builder.task_loader import PreparedBuildLoader
from audisor.operations.models import OperationResponse
from audisor.operations.store import SharedOperationStore
from audisor.schemas.build import BuildExecutionContext, BuildPlan, BuildRequest


def make_context(tmp_path):
    return BuildExecutionContext.seal(
        target_root=str(tmp_path),
        repository_identity={"root_reference": str(tmp_path), "revision": "HEAD", "dirty_state": "dirty"},
        allowed_write_paths=["src"],
        authority_limits={"mutation_authorized": False, "execution_authorized": False, "apply_authorized": False, "completion_claimed": False},
        workspace_identity={"workspace_id": "workspace-1", "root_reference": str(tmp_path)},
        success_definition={"required": ["tests pass"]},
        validation_requirements=[{"argv": ["python", "-m", "pytest"]}],
    )


def prepared(tmp_path):
    target = tmp_path / "target"
    (target / "src").mkdir(parents=True)
    porcelain.init(str(target))
    build_path = tmp_path / "build"
    build_path.mkdir()
    context = make_context(target)
    instruction = SimpleNamespace(build_id="build-1", instruction="build", execution_context=context)
    plan = SimpleNamespace(model_dump=lambda mode: {"build_id": "build-1", "status": "ready", "tasks": []})
    return SimpleNamespace(
        build_path=build_path,
        instruction=instruction,
        plan=plan,
        skills={"task-1": SimpleNamespace(content="skill")},
    )


class FakeLoader:
    def __init__(self, value):
        self.value = value

    def load(self, build_id):
        return self.value


def test_adapter_submits_persisted_context_and_launches_once(tmp_path):
    value = prepared(tmp_path)
    store = SharedOperationStore(tmp_path / "operations")
    captured = {}
    contract = value.build_path / "executions" / "op-1" / "workspace" / "audisor-artifacts"
    contract.mkdir(parents=True)
    (contract / "execution-contract.json").write_text("{}", encoding="utf-8")

    def accept(request):
        captured["request"] = request
        response = OperationResponse("op-1", "build", "codex", request.canonical_hash(), "accepted", True, True, "no_material_gap", "contract.json", (), {"apply": False}, {"permitted": True, "state": "permitted"})
        store.bind("op-1", request.canonical_hash(), {"operation_kind": "build"})
        store.persist_response("op-1", response.as_dict())
        return response

    launches = []

    def launch_codex(**kwargs):
        launches.append(kwargs)
        return 77, 0, "codex_completed", ("codex", "exec", "-")

    result = CodexAdapter(
        operation_store=store,
        loader=FakeLoader(value),
        operation_service=SimpleNamespace(accept=accept),
        launcher=launch_codex,
    ).run("build-1", operation_id="op-1")
    assert result.exit_code == 0
    assert len(launches) == 1
    assert captured["request"].build.request.target_root == str((tmp_path / "target").resolve())
    assert captured["request"].build.request.allowed_write_paths == ["src"]
    assert launches[0]["cwd"] == (tmp_path / "target").resolve()
    assert launches[0]["stdin_bytes"] == result.stdin_path.read_bytes()


def test_incomplete_prepared_build_fails_before_service(tmp_path):
    value = prepared(tmp_path)
    value.instruction.execution_context = None
    calls = []
    with pytest.raises(CodexAdapterError, match="prepared_build_contract_incomplete"):
        CodexAdapter(loader=FakeLoader(value), operation_service=SimpleNamespace(accept=lambda _: calls.append(1))).run("build-1")
    assert calls == []


def test_blocked_response_does_not_launch(tmp_path):
    value = prepared(tmp_path)
    store = SharedOperationStore(tmp_path / "operations")
    calls = []
    response = OperationResponse("op-1", "build", "codex", "a" * 64, "blocked", True, True, "material_gap_found", None, (), {}, {"permitted": False, "state": "blocked"})
    adapter = CodexAdapter(loader=FakeLoader(value), operation_store=store, operation_service=SimpleNamespace(accept=lambda _: response), launcher=lambda **_: calls.append(1))
    result = adapter.run("build-1", operation_id="op-1")
    assert result.status == "blocked"
    assert calls == []


def test_prepared_build_round_trips_execution_context_through_existing_integrity(tmp_path):
    target = tmp_path / "target"
    (target / "src").mkdir(parents=True)
    porcelain.init(str(target))
    context = make_context(target)
    store = BuildStore(tmp_path / "data")
    request = BuildRequest(build_id="build-context", instruction="build", execution_context=context)
    plan = BuildPlan.model_validate({
        "build_id": "build-context",
        "status": "ready",
        "gaps": [],
        "tasks": [{
            "task_id": "task-1",
            "title": "Write output",
            "depends_on": [],
            "prompt": """## Objective
Write the output.

## Inputs and repository paths
Use the prepared repository.

## Required work
Write the requested file.

## Ordered steps
1. Write the file.

## Expected output
Return the changed file.

## Validation
Run the validation command.

## Evidence to return
Return the changed path.""",
            "expected_outputs": ["src/output.txt"],
            "validation": [{"argv": ["python", "-c", "print(1)"]}],
        }],
    })
    store.publish(request, plan, render_skills(plan.build_id, plan.tasks))
    loaded = PreparedBuildLoader(store).load("build-context")
    assert loaded.instruction.execution_context == context
    assert loaded.instruction.execution_context.execution_context_sha256 == context.execution_context_sha256


def test_cli_registers_only_build_id_codex_command(tmp_path):
    class FakeAdapter:
        def run(self, build_id, *, operation_id=None):
            return CodexRunResult("op-1", build_id, SimpleNamespace(status="accepted"), tmp_path / "h", tmp_path / "s", "a" * 64, "b" * 64, 1, ("codex", "exec", "-"), tmp_path, 1, 0, "codex_completed")

    assert cli.main(["codex", "--build-id", "build-1"], codex_adapter=FakeAdapter()) == 0


def test_raw_task_enters_existing_preparer_then_adapter(tmp_path):
    target = tmp_path / "repo"
    (target / "src").mkdir(parents=True)
    (target / "tests").mkdir()
    porcelain.init(str(target))
    captured = {}

    class FakePreparer:
        def prepare(self, request):
            captured["request"] = request

    class FakeAdapter:
        def run(self, build_id):
            captured["build_id"] = build_id
            return "launched"

    result = prepare_and_run_task(
        "create the feature",
        target_root=target,
        preparer_factory=lambda store: FakePreparer(),
        adapter_factory=lambda: FakeAdapter(),
    )
    assert result == "launched"
    assert captured["request"].instruction == "create the feature"
    assert captured["request"].execution_context.target_root == str(target.resolve())
    assert captured["request"].execution_context.allowed_write_paths == ["src", "tests"]
    assert captured["build_id"].startswith("build-")


def test_build_id_preserves_byte_contract_under_python_311(tmp_path, monkeypatch):
    target = tmp_path / "repo"
    (target / "src").mkdir(parents=True)
    porcelain.init(str(target))
    captured = {}

    class FakePreparer:
        def prepare(self, request):
            captured["request"] = request

    class FakeAdapter:
        def run(self, build_id):
            captured["build_id"] = build_id
            return "launched"

    fixed = uuid.UUID("12345678123456781234567812345678")
    monkeypatch.setattr(uuid, "uuid4", lambda: fixed)
    task = "create the feature"

    result = prepare_and_run_task(
        task,
        target_root=target,
        preparer_factory=lambda store: FakePreparer(),
        adapter_factory=lambda: FakeAdapter(),
    )

    expected_digest = hashlib.sha256(
        task.encode("utf-8") + b"\0" + fixed.hex.encode("ascii")
    ).hexdigest()
    expected_build_id = f"build-{expected_digest[:20]}"

    assert result == "launched"
    assert captured["build_id"] == expected_build_id
    assert captured["request"].build_id == expected_build_id
