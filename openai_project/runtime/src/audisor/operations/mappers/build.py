from __future__ import annotations

from ..models import BuildOperationInput, OperationRequest


def map_build(request: OperationRequest) -> tuple[str, BuildOperationInput]:
    if request.operation_kind != "build" or request.build is None:
        raise ValueError("build_contract_invalid")
    request.build.validate(request.operation_id)
    return request.build.build_id, request.build
