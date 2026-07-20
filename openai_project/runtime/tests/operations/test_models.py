from types import SimpleNamespace

import pytest

from audisor.operations.models import (
    BuildOperationInput,
    ClientMetadata,
    FixOperationInput,
    OperationRequest,
    OperationValidationError,
)
from audisor.schemas.execution import BuildExecutionRequest


def build_request(execution_id="op-1"):
    return BuildExecutionRequest(execution_id=execution_id, idempotency_key=execution_id, target_root="C:/target", allowed_write_paths=["src"])


def client():
    return ClientMetadata("client", "adapter", "1.0")


def test_build_and_fix_payloads_are_mutually_exclusive():
    operation = SimpleNamespace(operation_id="op-1", findings=["finding"])
    request = OperationRequest("op-1", "build", client(), {}, {}, BuildOperationInput("build-1", build_request()), FixOperationInput(operation))
    with pytest.raises(OperationValidationError):
        request.validate()


def test_client_cannot_supply_continuation_or_authority():
    request = OperationRequest("op-1", "build", client(), {}, {}, BuildOperationInput("build-1", build_request()), delivery={"continuation": {}})
    with pytest.raises(OperationValidationError):
        request.validate()


def test_build_operation_identity_is_bound_to_execution_id():
    request = OperationRequest("op-1", "build", client(), {}, {}, BuildOperationInput("build-1", build_request("other")))
    with pytest.raises(OperationValidationError):
        request.validate()
