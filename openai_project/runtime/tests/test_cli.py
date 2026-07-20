from __future__ import annotations

import io
import json

from audisor import cli
from audisor.operations.models import OperationResponse
from audisor.operations.service import AcceptedOperationService
from audisor.operations.store import SharedOperationStore
from audisor.ollama_setup import OllamaSetupError


def test_audisor_commands_persist_and_report_state(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("AUDISOR_CONFIG_PATH", str(tmp_path / "config.json"))
    assert cli.main(["aflow", "off"]) == 0
    assert cli.main(["aflow", "status"]) == 0
    assert capsys.readouterr().out.strip() == "A-Flow: OFF"
    assert cli.main(["aflow", "on"]) == 0
    assert cli.main(["aflow", "status"]) == 0
    assert capsys.readouterr().out.strip() == "A-Flow: ON"


def test_setup_enables_aflow_only_after_success(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("AUDISOR_CONFIG_PATH", str(tmp_path / "config.json"))
    monkeypatch.setattr(cli, "setup_ollama", lambda: type("Result", (), {"model": "qwen2.5-coder:7b"})())
    assert cli.main(["setup"]) == 0
    assert "Connection: verified" in capsys.readouterr().out
    assert cli.main(["aflow", "status"]) == 0
    assert capsys.readouterr().out.strip() == "A-Flow: ON"


def test_setup_does_not_enable_aflow_before_live_verification(tmp_path, monkeypatch, capsys) -> None:
    path = tmp_path / "config.json"
    monkeypatch.setenv("AUDISOR_CONFIG_PATH", str(path))
    monkeypatch.setattr(cli, "setup_ollama", lambda: (_ for _ in ()).throw(OllamaSetupError("Model verification failed")))
    assert cli.main(["setup"]) == 1
    assert "Model verification failed" in capsys.readouterr().err
    assert not path.exists()


def build_payload(operation_id="op-1"):
    return {
        "operation_id": operation_id,
        "operation_kind": "build",
        "client": {"client_id": "test-client", "adapter_id": "test-adapter", "adapter_version": "1"},
        "repository": {"root_reference": "repo"},
        "requested_scope": {"paths": ["src"]},
        "build": {"build_id": "build-1", "request": {"execution_id": operation_id, "idempotency_key": operation_id, "target_root": "C:/target", "allowed_write_paths": ["src"]}},
    }


class Service:
    def __init__(self):
        self.calls = 0

    def accept(self, request):
        self.calls += 1
        return OperationResponse(request.operation_id, request.operation_kind, request.client.client_id, request.canonical_hash(), "accepted", None, None, "no_material_gap", authority_limits={"apply": False}, continuation={"permitted": True, "state": "permitted"})


def test_host_accept_reads_stdin_and_emits_only_canonical_response():
    service = Service()
    output, error = io.StringIO(), io.StringIO()
    assert cli.main(["host", "accept"], operation_service=service, stdin=io.StringIO(json.dumps(build_payload())), stdout=output, stderr=error) == 0
    assert json.loads(output.getvalue())["status"] == "accepted"
    assert error.getvalue() == ""
    assert service.calls == 1


def test_host_accept_reads_request_file(tmp_path):
    path = tmp_path / "operation.json"
    path.write_text(json.dumps(build_payload()), encoding="utf-8")
    service = Service()
    output, error = io.StringIO(), io.StringIO()
    assert cli.main(["host", "accept", "--request-file", str(path)], operation_service=service, stdin=io.StringIO(""), stdout=output, stderr=error) == 0
    assert json.loads(output.getvalue())["operation_id"] == "op-1"


def test_host_accept_rejects_multiple_sources_and_invalid_json_without_service_call(tmp_path):
    service = Service()
    output, error = io.StringIO(), io.StringIO()
    path = tmp_path / "operation.json"
    path.write_text("{}", encoding="utf-8")
    assert cli.main(["host", "accept", "--request-file", str(path)], operation_service=service, stdin=io.StringIO("{}"), stdout=output, stderr=error) == 2
    assert service.calls == 0
    output, error = io.StringIO(), io.StringIO()
    assert cli.main(["host", "accept"], operation_service=service, stdin=io.StringIO("not-json"), stdout=output, stderr=error) == 2
    assert service.calls == 0


def test_host_accept_rejects_unknown_fields_without_service_call():
    service = Service()
    payload = build_payload()
    payload["authority"] = {"apply": True}
    output, error = io.StringIO(), io.StringIO()
    assert cli.main(["host", "accept"], operation_service=service, stdin=io.StringIO(json.dumps(payload)), stdout=output, stderr=error) == 2
    assert service.calls == 0


def test_host_accept_duplicate_and_conflict_use_shared_service(tmp_path):
    calls = []
    host = type("Host", (), {"execute": lambda self, build_id, request: calls.append((build_id, request.execution_id)) or {"status": "accepted"}})()
    service = AcceptedOperationService(SharedOperationStore(tmp_path), build_executor=host, fix_dispatcher=object(), fix_continue=lambda *_: None, fix_finalize=lambda *_: None)
    payload = json.dumps(build_payload())
    for _ in range(2):
        output, error = io.StringIO(), io.StringIO()
        assert cli.main(["host", "accept"], operation_service=service, stdin=io.StringIO(payload), stdout=output, stderr=error) == 0
    assert len(calls) == 1
    changed = build_payload()
    changed["build"]["request"]["target_root"] = "C:/other"
    output, error = io.StringIO(), io.StringIO()
    assert cli.main(["host", "accept"], operation_service=service, stdin=io.StringIO(json.dumps(changed)), stdout=output, stderr=error) == 3
    assert len(calls) == 1
