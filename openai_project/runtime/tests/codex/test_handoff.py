from __future__ import annotations

from types import SimpleNamespace

from audisor.codex.handoff import build_handoff, canonical_bytes, persist_handoff, sha256_bytes
from audisor.schemas.build import BuildExecutionContext


def context(tmp_path):
    return BuildExecutionContext.seal(
        target_root=str(tmp_path),
        repository_identity={"root_reference": str(tmp_path), "revision": "HEAD", "dirty_state": "clean"},
        allowed_write_paths=["src"],
        authority_limits={"mutation_authorized": False, "execution_authorized": False, "apply_authorized": False, "completion_claimed": False},
        workspace_identity={"workspace_id": "workspace-1", "root_reference": str(tmp_path)},
        success_definition={"required": ["tests pass"]},
        validation_requirements=[{"argv": ["python", "-m", "pytest"]}],
    )


def test_handoff_and_stdin_hashes_are_hashes_of_persisted_bytes(tmp_path):
    prepared = SimpleNamespace(
        instruction=SimpleNamespace(execution_context=context(tmp_path), instruction="build"),
        plan=SimpleNamespace(model_dump=lambda mode: {"build_id": "b1", "tasks": []}),
        skills={"task-1": SimpleNamespace(content="skill")},
    )
    response = SimpleNamespace(execution_contract_reference="contract.json", artifact_references=(), authority_limits={"apply": False})
    handoff = build_handoff(operation_id="op-1", build_id="b1", client={"client_id": "codex"}, prepared=prepared, response=response)
    handoff_path, stdin_path, handoff_hash, stdin_hash, size = persist_handoff(tmp_path / "artifacts", handoff)
    assert sha256_bytes(handoff_path.read_bytes()) == handoff_hash
    assert sha256_bytes(stdin_path.read_bytes()) == stdin_hash
    assert size == len(stdin_path.read_bytes())
    assert canonical_bytes(handoff) in stdin_path.read_bytes()
    assert "api_key" not in stdin_path.read_text().casefold()
