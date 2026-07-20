from types import SimpleNamespace

import pytest

from audisor.operations.mappers.fix import map_fix
from audisor.operations.models import ClientMetadata, FixOperationInput, OperationRequest, OperationValidationError


def test_fix_mapping_requires_authoritative_findings():
    operation = SimpleNamespace(operation_id="op-1", findings=["finding"])
    request = OperationRequest("op-1", "fix", ClientMetadata("c", "a", "1"), {}, {}, fix=FixOperationInput(operation))
    assert map_fix(request).operation is operation

    empty = SimpleNamespace(operation_id="op-1", findings=[])
    with pytest.raises(OperationValidationError, match="fix_findings_missing"):
        map_fix(OperationRequest("op-1", "fix", ClientMetadata("c", "a", "1"), {}, {}, fix=FixOperationInput(empty)))
