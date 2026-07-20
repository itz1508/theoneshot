from __future__ import annotations

from ..models import FixOperationInput, OperationRequest


def map_fix(request: OperationRequest) -> FixOperationInput:
    if request.operation_kind != "fix" or request.fix is None:
        raise ValueError("fix_contract_invalid")
    request.fix.validate(request.operation_id)
    return request.fix
