from types import SimpleNamespace

from audisor.operations.models import BuildOperationInput, ClientMetadata, FixOperationInput, OperationRequest
from audisor.operations.service import AcceptedOperationService
from audisor.operations.store import SharedOperationStore
from audisor.schemas.execution import BuildExecutionRequest


class Host:
    def __init__(self):
        self.calls = 0

    def execute(self, build_id, request):
        self.calls += 1
        return {"status": "accepted", "decision_state": "no_material_gap", "authority_limits": {"apply": False}}


def request():
    host_request = BuildExecutionRequest(execution_id="op-1", idempotency_key="op-1", target_root="C:/target", allowed_write_paths=["src"])
    return OperationRequest("op-1", "build", ClientMetadata("client", "adapter", "1.0"), {"root_reference": "repo"}, {"paths": ["src"]}, BuildOperationInput("build-1", host_request))


def test_service_routes_once_and_duplicate_returns_existing(tmp_path):
    host = Host()
    service = AcceptedOperationService(SharedOperationStore(tmp_path), build_executor=host, fix_dispatcher=object(), fix_continue=lambda *_: None, fix_finalize=lambda *_: None)
    first = service.accept(request())
    second = service.accept(request())
    assert first.status == "accepted"
    assert second.status == "existing"
    assert second.existing_result is True
    assert host.calls == 1
    stored = (tmp_path / "op-1.json").read_text()
    assert '"adapter_id": "adapter"' in stored
    assert '"apply": false' in stored


def test_service_normalizes_host_failure_without_ignite_ownership(tmp_path):
    class FailingHost:
        def execute(self, *_):
            raise RuntimeError("host unavailable")

    service = AcceptedOperationService(SharedOperationStore(tmp_path), build_executor=FailingHost(), fix_dispatcher=object(), fix_continue=lambda *_: None, fix_finalize=lambda *_: None)
    response = service.accept(request())
    assert response.status == "failed"
    assert response.continuation["permitted"] is False


def test_service_routes_fix_once(tmp_path):
    operation = SimpleNamespace(operation_id="op-fix", findings=["finding"])
    calls = []
    dispatcher = type("Dispatcher", (), {"dispatch": lambda self, op, cont, final: calls.append(op.operation_id) or {"status": "blocked", "decision_state": "material_gap_found"}})()
    request_value = OperationRequest("op-fix", "fix", ClientMetadata("client", "adapter", "1"), {}, {}, fix=FixOperationInput(operation))
    service = AcceptedOperationService(SharedOperationStore(tmp_path), build_executor=object(), fix_dispatcher=dispatcher, fix_continue=lambda *_: None, fix_finalize=lambda *_: None)
    response = service.accept(request_value)
    assert response.status == "blocked"
    assert response.continuation["permitted"] is False
    assert calls == ["op-fix"]
