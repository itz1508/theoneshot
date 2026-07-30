"""Schema drift test — assistant-backend and OperationController schemas match.

Proves that both the canonical tool contracts (consumed by OperationController)
and the assistant-backend registry emit semantically identical schemas for
the same tool. Any future drift in names, parameters, required fields, or
descriptions will fail this test.
"""
from __future__ import annotations

import pytest

from operation_controller.tool_contracts import (
    all_canonical_tools,
    to_openai_tool_schema,
)

from audisor_assistant.tools.registry import default_registry
# Ensure all tool definitions are registered (import side-effect)
import audisor_assistant.tools.definitions.file_read  # noqa: F401
import audisor_assistant.tools.definitions.list_directory  # noqa: F401
import audisor_assistant.tools.definitions.shell_exec  # noqa: F401
import audisor_assistant.tools.definitions.audisor_scan  # noqa: F401


# Tools that should be present in both surfaces
_SHARED_TOOLS = {"file_read", "list_directory", "shell_exec", "audisor_scan"}


class TestSchemaDrift:
    """Assistant-backend registry and canonical contracts must not drift."""

    def test_all_shared_tools_registered(self) -> None:
        """Every production tool exists in the assistant-backend registry."""
        registry_names = {t.name for t in default_registry.all_tools()}
        for name in _SHARED_TOOLS:
            assert name in registry_names, f"Tool {name!r} missing from registry"

    def test_tool_names_match(self) -> None:
        """Registry tool names match canonical tool names for shared tools."""
        registry_names = {t.name for t in default_registry.all_tools()}
        canonical_names = set(all_canonical_tools().keys())
        # The registry may have a subset or superset, but shared tools must match
        overlap = registry_names & canonical_names
        assert overlap >= _SHARED_TOOLS

    @pytest.mark.parametrize("tool_name", sorted(_SHARED_TOOLS))
    def test_name_matches(self, tool_name: str) -> None:
        """Tool name in registry matches canonical name."""
        reg_tool = default_registry.get(tool_name)
        assert reg_tool is not None
        canonical = all_canonical_tools()[tool_name]
        assert reg_tool.name == canonical["name"]

    @pytest.mark.parametrize("tool_name", sorted(_SHARED_TOOLS))
    def test_description_matches(self, tool_name: str) -> None:
        """Tool description in registry matches canonical description."""
        reg_tool = default_registry.get(tool_name)
        assert reg_tool is not None
        canonical = all_canonical_tools()[tool_name]
        assert reg_tool.description == canonical["description"]

    @pytest.mark.parametrize("tool_name", sorted(_SHARED_TOOLS))
    def test_parameters_match(self, tool_name: str) -> None:
        """Tool parameters (schema) in registry match canonical parameters."""
        reg_tool = default_registry.get(tool_name)
        assert reg_tool is not None
        canonical = all_canonical_tools()[tool_name]
        assert reg_tool.parameters == canonical["parameters"]

    @pytest.mark.parametrize("tool_name", sorted(_SHARED_TOOLS))
    def test_requires_approval_matches(self, tool_name: str) -> None:
        """Approval requirement in registry matches canonical contract."""
        reg_tool = default_registry.get(tool_name)
        assert reg_tool is not None
        canonical = all_canonical_tools()[tool_name]
        assert reg_tool.requires_approval == canonical["requires_approval"]

    @pytest.mark.parametrize("tool_name", sorted(_SHARED_TOOLS))
    def test_read_only_matches(self, tool_name: str) -> None:
        """Read-only flag in registry matches canonical contract."""
        reg_tool = default_registry.get(tool_name)
        assert reg_tool is not None
        canonical = all_canonical_tools()[tool_name]
        assert reg_tool.read_only == canonical["read_only"]

    @pytest.mark.parametrize("tool_name", sorted(_SHARED_TOOLS))
    def test_provider_schema_matches(self, tool_name: str) -> None:
        """OpenAI-compatible schemas from both paths are identical."""
        reg_tool = default_registry.get(tool_name)
        assert reg_tool is not None
        canonical = all_canonical_tools()[tool_name]

        registry_schema = reg_tool.to_provider_schema()
        canonical_schema = to_openai_tool_schema(canonical)

        assert registry_schema == canonical_schema

    @pytest.mark.parametrize("tool_name", sorted(_SHARED_TOOLS))
    def test_additional_properties_false_in_both(self, tool_name: str) -> None:
        """Both surfaces enforce additionalProperties: false."""
        reg_tool = default_registry.get(tool_name)
        assert reg_tool is not None
        assert reg_tool.parameters.get("additionalProperties") is False

        canonical = all_canonical_tools()[tool_name]
        assert canonical["parameters"].get("additionalProperties") is False


class TestCanonicalImportPath:
    """Prove that the canonical import always succeeds in the test environment.

    The assistant_backend tool definition files use a try/except ImportError
    fallback. This test asserts the canonical import path works, so the
    fallback is never exercised in tests. If this test fails, it means
    operation_controller is not importable and the fallback definitions
    are being used — which risks drift.
    """

    def test_canonical_import_succeeds(self) -> None:
        """The canonical tool_contracts module is importable."""
        from operation_controller.tool_contracts import get_canonical_tool
        # Verify we can retrieve each shared tool
        for name in _SHARED_TOOLS:
            tool = get_canonical_tool(name)
            assert tool["name"] == name

    def test_registry_tools_use_canonical_descriptions(self) -> None:
        """Registry tools use canonical descriptions (not fallbacks)."""
        from operation_controller.tool_contracts import get_canonical_tool
        for name in _SHARED_TOOLS:
            reg_tool = default_registry.get(name)
            assert reg_tool is not None
            canonical = get_canonical_tool(name)
            # If the canonical import worked, descriptions must match
            assert reg_tool.description == canonical["description"], (
                f"Tool {name!r} description mismatch — fallback may be active"
            )
