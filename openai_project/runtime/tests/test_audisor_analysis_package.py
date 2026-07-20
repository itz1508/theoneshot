from __future__ import annotations

import json
from pathlib import Path

import pytest

from audisor.audisor_lifecycle.analysis_package import (
    AnalysisPackageError,
    assemble_analysis_package,
    package_sha256,
)


FIXTURE_ROOT = (
    Path(__file__).resolve().parents[2]
    / "aflow"
    / "tests"
    / "fixtures"
    / "05-fully-proven"
    / "input"
)


def frozen_request() -> dict:
    return {
        "schema_version": "1.0.0",
        "analysis_id": "operation.build-001",
        "success_definition": json.loads((FIXTURE_ROOT / "success-definition.json").read_text()),
        "plan": json.loads((FIXTURE_ROOT / "plan.json").read_text()),
        "authority_evidence": json.loads((FIXTURE_ROOT / "authority-evidence.json").read_text()),
        "repository_evidence": json.loads((FIXTURE_ROOT / "repository-evidence.json").read_text()),
        "baseline": json.loads((FIXTURE_ROOT / "baseline.json").read_text()),
        "evidence": json.loads((FIXTURE_ROOT / "evidence.json").read_text()),
    }


def test_assembles_and_seals_exact_frozen_request() -> None:
    request = frozen_request()
    package = assemble_analysis_package(
        operation_id="operation.build-001",
        operation_type="build",
        accepted_task={"objective": "test"},
        accepted_plan={"plan_id": "plan.fixture"},
        authority_context={"authority": "host"},
        analysis_request=request,
        repository_context={"scope": "scoped"},
        workspace_identity={"workspace_id": "workspace-001"},
        provider_policy={"provider": "local-openai-compatible", "model_id": "qwen2.5-coder:7b"},
    )

    assert package.model_input["analysis_id"] == "operation.build-001"
    assert package.package_hash == package_sha256(package.canonical_payload)
    assert package.analysis_request["plan"]["validations"]
    assert package.analysis_request["success_definition"]["proof_obligations"]


def test_package_is_bound_to_host_operation_and_is_immutable() -> None:
    package = assemble_analysis_package(
        operation_id="operation.build-001",
        operation_type="build",
        accepted_task={"objective": "test"},
        accepted_plan={"plan_id": "plan.fixture"},
        authority_context={"authority": "host"},
        analysis_request=frozen_request(),
        repository_context={"scope": "scoped"},
        workspace_identity={"workspace_id": "workspace-001"},
        provider_policy={"provider": "local-openai-compatible"},
    )

    with pytest.raises(TypeError):
        package.analysis_request["analysis_id"] = "other"  # type: ignore[index]
    with pytest.raises(TypeError):
        package.analysis_request["plan"]["objective"] = "other"  # type: ignore[index]


def test_unknown_request_properties_are_rejected() -> None:
    request = frozen_request()
    request["trajectory"] = []

    with pytest.raises(AnalysisPackageError, match="schema validation"):
        assemble_analysis_package(
            operation_id="operation.build-001",
            operation_type="build",
            accepted_task={"objective": "test"},
            accepted_plan={"plan_id": "plan.fixture"},
            authority_context={"authority": "host"},
            analysis_request=request,
            repository_context={"scope": "scoped"},
            workspace_identity={"workspace_id": "workspace-001"},
            provider_policy={"provider": "local-openai-compatible"},
        )


def test_analysis_id_must_match_host_operation_id() -> None:
    with pytest.raises(AnalysisPackageError, match="operation_id"):
        assemble_analysis_package(
            operation_id="operation.other",
            operation_type="build",
            accepted_task={"objective": "test"},
            accepted_plan={"plan_id": "plan.fixture"},
            authority_context={"authority": "host"},
            analysis_request=frozen_request(),
            repository_context={"scope": "scoped"},
            workspace_identity={"workspace_id": "workspace-001"},
            provider_policy={"provider": "local-openai-compatible"},
        )
