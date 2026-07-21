"""Adversarial tests for deterministic validation grounding.

Ensures that free-form model text cannot bypass the grounding resolver,
and command authority is only granted via exact stable source IDs.
"""

from pathlib import Path
import pytest

from audisor_backend.policies.fix.validation_grounding import (
    ValidationGroundingResolver,
    build_validation_source_catalog,
    GroundingError,
    ValidationSourceCatalog,
)
from audisor_backend.schemas.fix.models import (
    Finding,
    FindingCheck,
    FindingsList,
    FixScopedManifest,
    ImplementationPlan,
    SuccessDefinition,
    ValidationSpec,
)

@pytest.fixture
def base_context(tmp_path: Path):
    finding = Finding(
        id="F-1",
        type="syntax",
        file="src/app.py",
        severity="high",
        evidence={"command": ["pytest", "tests/test_app.py"]}
    )
    findings = FindingsList([finding])
    manifest = FixScopedManifest(
        files=["src/app.py"],
        dependency_closure=["src/app.py", "tests/test_app.py"],
        input_hash="abc",
        file_hashes={"src/app.py": "123", "tests/test_app.py": "456"},
    )
    plan = ImplementationPlan(
        steps=[], target_files=["src/app.py"], is_qualified=True, minor_issues=[]
    )
    catalog = build_validation_source_catalog(
        repository_root=tmp_path,
        findings=findings,
        manifest=manifest,
        plan=plan,
        configured_test_commands=[("pytest", "tests/")]
    )
    return tmp_path, findings, manifest, plan, catalog


def resolve(tmp_path, findings, manifest, plan, catalog, validations=None, finding_checks=None):
    resolver = ValidationGroundingResolver()
    success_def = SuccessDefinition(
        finding_checks=finding_checks or [],
        validations=validations or [],
        must_not_regress=[],
        success_rule="all_finding_checks_and_validations_pass"
    )
    return resolver.resolve(
        repository_root=tmp_path,
        findings=findings,
        manifest=manifest,
        plan=plan,
        success_definition=success_def,
        catalog=catalog
    )


def test_valid_grounding(base_context):
    tmp_path, findings, manifest, plan, catalog = base_context
    validations = [ValidationSpec(id="V-1", command_or_assertion="configured-test-001", expected_result="pass")]
    result = resolve(tmp_path, findings, manifest, plan, catalog, validations=validations)
    assert len(result.validations) == 1
    assert result.validations[0].source_type == "configured_test"
    assert result.validations[0].authorized_tokens == ("pytest", "tests/")


def test_adversarial_same_text_with_altered_quoting(base_context):
    tmp_path, findings, manifest, plan, catalog = base_context
    val = ValidationSpec(id="V-1", command_or_assertion="\\\"pytest\\\" 'tests/'", expected_result="pass")
    with pytest.raises(GroundingError, match="cannot be grounded"):
        resolve(tmp_path, findings, manifest, plan, catalog, validations=[val])


def test_adversarial_added_shell_operator(base_context):
    tmp_path, findings, manifest, plan, catalog = base_context
    val = ValidationSpec(id="V-1", command_or_assertion="pytest tests/ && echo pwned", expected_result="pass")
    with pytest.raises(GroundingError, match="cannot be grounded"):
        resolve(tmp_path, findings, manifest, plan, catalog, validations=[val])


def test_adversarial_appended_extra_command(base_context):
    tmp_path, findings, manifest, plan, catalog = base_context
    val = ValidationSpec(id="V-1", command_or_assertion="pytest tests/; rm -rf /", expected_result="pass")
    with pytest.raises(GroundingError, match="cannot be grounded"):
        resolve(tmp_path, findings, manifest, plan, catalog, validations=[val])


def test_adversarial_executable_substitution(base_context):
    tmp_path, findings, manifest, plan, catalog = base_context
    val = ValidationSpec(id="V-1", command_or_assertion="malicious_pytest tests/", expected_result="pass")
    with pytest.raises(GroundingError, match="cannot be grounded"):
        resolve(tmp_path, findings, manifest, plan, catalog, validations=[val])


def test_adversarial_unknown_source_id(base_context):
    tmp_path, findings, manifest, plan, catalog = base_context
    val = ValidationSpec(id="V-1", command_or_assertion="configured-test-999", expected_result="pass")
    with pytest.raises(GroundingError, match="cannot be grounded"):
        resolve(tmp_path, findings, manifest, plan, catalog, validations=[val])


def test_adversarial_source_id_from_different_finding(base_context):
    tmp_path, findings, manifest, plan, catalog = base_context
    from audisor_backend.policies.fix.validation_grounding import RecordedTestSource
    catalog.recorded_tests.append(
        RecordedTestSource(source_id="recorded-test-F-2-001", finding_id="F-2", tokens=("echo",))
    )
    fc = FindingCheck(finding_id="F-1", resolution_method="test", check="recorded-test-F-2-001", expected_result="pass")
    with pytest.raises(GroundingError, match="cannot be grounded"):
        resolve(tmp_path, findings, manifest, plan, catalog, finding_checks=[fc])


def test_adversarial_configured_source_reused_as_recorded_source(base_context):
    tmp_path, findings, manifest, plan, catalog = base_context
    fc = FindingCheck(finding_id="F-1", resolution_method="test", check="configured-test-001", expected_result="pass")
    with pytest.raises(GroundingError, match="cannot be grounded"):
        resolve(tmp_path, findings, manifest, plan, catalog, finding_checks=[fc])


def test_adversarial_model_provided_raw_command_matching_only_after_reparsing(base_context):
    tmp_path, findings, manifest, plan, catalog = base_context
    val = ValidationSpec(id="V-1", command_or_assertion="pytest tests/", expected_result="pass")
    with pytest.raises(GroundingError, match="cannot be grounded"):
        resolve(tmp_path, findings, manifest, plan, catalog, validations=[val])


def test_serialization_roundtrip(base_context):
    tmp_path, findings, manifest, plan, catalog = base_context
    val = ValidationSpec(id="V-1", command_or_assertion="configured-test-001", expected_result="pass")
    result = resolve(tmp_path, findings, manifest, plan, catalog, validations=[val])
    mapping = result.to_mapping()
    assert mapping["validations"][0]["source_reference"] == "configured-test-001"
    assert mapping["validations"][0]["source_type"] == "configured_test"
    assert mapping["validations"][0]["authorized_tokens"] == ["pytest", "tests/"]

    cat_mapping = catalog.to_mapping()
    assert cat_mapping["configured_tests"][0]["source_id"] == "configured-test-001"
    assert cat_mapping["configured_tests"][0]["authorized_tokens"] == ["pytest", "tests/"]
