from audisor.operations.mappers.build import map_build
from audisor.operations.models import BuildOperationInput, ClientMetadata, OperationRequest
from audisor.schemas.execution import BuildExecutionRequest


def test_build_mapping_preserves_existing_host_request():
    host_request = BuildExecutionRequest(execution_id="op-1", idempotency_key="op-1", target_root="C:/target", allowed_write_paths=["src"])
    request = OperationRequest("op-1", "build", ClientMetadata("c", "a", "1"), {}, {}, BuildOperationInput("build-1", host_request))
    build_id, mapped = map_build(request)
    assert build_id == "build-1"
    assert mapped.request is host_request
