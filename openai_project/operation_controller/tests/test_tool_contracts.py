"""Tests for canonical tool contracts — tool_contracts.py.

Proves:
- All production tool schemas have additionalProperties: false.
- audisor_scan canonical parameters match the executor (target + depth).
- Unknown tool raises KeyError.
- Tool contracts have all required fields.
- to_openai_tool_schema strips runtime metadata.
- Read-only tools do not require approval.
- shell_exec requires approval.
"""
from __future__ import annotations

import pytest

from operation_controller.tool_contracts import (
    CAPABILITY_ANALYSE,
    CAPABILITY_EXECUTE,
    CAPABILITY_READ,
    CAPABILITY_MUTATE,
    OWNER_BACKEND,
    OWNER_FRONTEND,
    all_canonical_tools,
    canonical_tool_names,
    capability,
    execution_owner,
    get_canonical_tool,
    production_tool_definitions,
    requires_approval,
    to_openai_tool_schema,
    to_openai_tool_schemas,
)


# ─── Required fields ─────────────────────────────────────────────────────────

_REQUIRED_FIELDS = {"name", "description", "parameters", "capability",
                    "execution_owner", "requires_approval", "read_only"}

_VALID_CAPABILITIES = {CAPABILITY_READ, CAPABILITY_EXECUTE, CAPABILITY_MUTATE, CAPABILITY_ANALYSE}
_VALID_OWNERS = {OWNER_FRONTEND, OWNER_BACKEND}


class TestCanonicalToolShape:
    """Every canonical tool has the required fields and valid values."""

    @pytest.mark.parametrize("name", [
        "file_read", "list_directory", "shell_exec", "file_write", "audisor_scan",
    ])
    def test_has_all_required_fields(self, name: str) -> None:
        tool = get_canonical_tool(name)
        for field in _REQUIRED_FIELDS:
            assert field in tool, f"Tool {name!r} missing field {field!r}"

    @pytest.mark.parametrize("name", [
        "file_read", "list_directory", "shell_exec", "file_write", "audisor_scan",
    ])
    def test_capability_is_valid(self, name: str) -> None:
        tool = get_canonical_tool(name)
        assert tool["capability"] in _VALID_CAPABILITIES

    @pytest.mark.parametrize("name", [
        "file_read", "list_directory", "shell_exec", "file_write", "audisor_scan",
    ])
    def test_execution_owner_is_valid(self, name: str) -> None:
        tool = get_canonical_tool(name)
        assert tool["execution_owner"] in _VALID_OWNERS

    @pytest.mark.parametrize("name", [
        "file_read", "list_directory", "shell_exec", "file_write", "audisor_scan",
    ])
    def test_name_matches_key(self, name: str) -> None:
        tool = get_canonical_tool(name)
        assert tool["name"] == name


# ─── additionalProperties: false ─────────────────────────────────────────────


class TestAdditionalPropertiesFalse:
    """All production tool schemas reject unknown parameters."""

    @pytest.mark.parametrize("name", [
        "file_read", "list_directory", "shell_exec", "file_write", "audisor_scan",
    ])
    def test_additional_properties_false(self, name: str) -> None:
        tool = get_canonical_tool(name)
        params = tool["parameters"]
        assert params.get("type") == "object"
        assert params.get("additionalProperties") is False


# ─── audisor_scan parameter truth ────────────────────────────────────────────


class TestAudisorScanParameters:
    """audisor_scan uses target + depth (matching executor truth)."""

    def test_has_target_parameter(self) -> None:
        tool = get_canonical_tool("audisor_scan")
        props = tool["parameters"]["properties"]
        assert "target" in props
        assert props["target"]["type"] == "string"

    def test_has_depth_parameter(self) -> None:
        tool = get_canonical_tool("audisor_scan")
        props = tool["parameters"]["properties"]
        assert "depth" in props
        assert props["depth"]["type"] == "string"
        assert "enum" in props["depth"]
        assert set(props["depth"]["enum"]) == {"surface", "standard", "deep"}

    def test_target_is_required(self) -> None:
        tool = get_canonical_tool("audisor_scan")
        assert "target" in tool["parameters"]["required"]

    def test_depth_is_optional_with_default(self) -> None:
        tool = get_canonical_tool("audisor_scan")
        assert "depth" not in tool["parameters"]["required"]
        assert tool["parameters"]["properties"]["depth"].get("default") == "standard"

    def test_no_query_parameter(self) -> None:
        """The canonical contract uses target, not query."""
        tool = get_canonical_tool("audisor_scan")
        props = tool["parameters"]["properties"]
        assert "query" not in props


# ─── Unknown tool rejection ─────────────────────────────────────────────────


class TestUnknownToolRejection:
    """get_canonical_tool raises KeyError for unknown tools."""

    def test_unknown_tool_raises_key_error(self) -> None:
        with pytest.raises(KeyError):
            get_canonical_tool("nonexistent_tool")

    def test_empty_name_raises_key_error(self) -> None:
        with pytest.raises(KeyError):
            get_canonical_tool("")


# ─── Approval classification ────────────────────────────────────────────────


class TestApprovalClassification:
    """Read-only tools do not require approval; shell_exec does."""

    def test_file_read_no_approval(self) -> None:
        assert requires_approval("file_read") is False

    def test_list_directory_no_approval(self) -> None:
        assert requires_approval("list_directory") is False

    def test_audisor_scan_no_approval(self) -> None:
        assert requires_approval("audisor_scan") is False

    def test_shell_exec_requires_approval(self) -> None:
        assert requires_approval("shell_exec") is True

    def test_file_write_requires_approval(self) -> None:
        # Defined in the contract even though it's prohibited in production
        assert requires_approval("file_write") is True

    def test_unknown_tool_no_approval(self) -> None:
        assert requires_approval("nonexistent") is False


# ─── Execution owner classification ─────────────────────────────────────────


class TestExecutionOwnerClassification:

    def test_file_read_is_frontend(self) -> None:
        assert execution_owner("file_read") == OWNER_FRONTEND

    def test_list_directory_is_frontend(self) -> None:
        assert execution_owner("list_directory") == OWNER_FRONTEND

    def test_shell_exec_is_frontend(self) -> None:
        assert execution_owner("shell_exec") == OWNER_FRONTEND

    def test_audisor_scan_is_backend(self) -> None:
        assert execution_owner("audisor_scan") == OWNER_BACKEND

    def test_unknown_tool_owner_is_none(self) -> None:
        assert execution_owner("nonexistent") is None


# ─── Capability classification ───────────────────────────────────────────────


class TestCapabilityClassification:

    def test_file_read_is_read(self) -> None:
        assert capability("file_read") == CAPABILITY_READ

    def test_list_directory_is_read(self) -> None:
        assert capability("list_directory") == CAPABILITY_READ

    def test_shell_exec_is_execute(self) -> None:
        assert capability("shell_exec") == CAPABILITY_EXECUTE

    def test_file_write_is_mutate(self) -> None:
        assert capability("file_write") == CAPABILITY_MUTATE

    def test_audisor_scan_is_analyse(self) -> None:
        assert capability("audisor_scan") == CAPABILITY_ANALYSE


# ─── OpenAI schema conversion ────────────────────────────────────────────────


class TestOpenAISchemaConversion:
    """to_openai_tool_schema strips runtime metadata."""

    def test_strips_capability(self) -> None:
        tool = get_canonical_tool("file_read")
        schema = to_openai_tool_schema(tool)
        assert "capability" not in schema
        assert "function" in schema
        assert "capability" not in schema["function"]

    def test_strips_execution_owner(self) -> None:
        tool = get_canonical_tool("shell_exec")
        schema = to_openai_tool_schema(tool)
        assert "execution_owner" not in schema
        assert "function" in schema

    def test_strips_requires_approval(self) -> None:
        tool = get_canonical_tool("shell_exec")
        schema = to_openai_tool_schema(tool)
        assert "requires_approval" not in schema

    def test_strips_read_only(self) -> None:
        tool = get_canonical_tool("file_read")
        schema = to_openai_tool_schema(tool)
        assert "read_only" not in schema

    def test_preserves_name_description_parameters(self) -> None:
        tool = get_canonical_tool("shell_exec")
        schema = to_openai_tool_schema(tool)
        fn = schema["function"]
        assert fn["name"] == "shell_exec"
        assert fn["description"] == tool["description"]
        assert fn["parameters"] == tool["parameters"]

    def test_to_openai_tool_schemas_returns_sorted(self) -> None:
        schemas = to_openai_tool_schemas()
        names = [s["function"]["name"] for s in schemas]
        assert names == sorted(names)
        assert set(names) == canonical_tool_names()


# ─── Registry functions ──────────────────────────────────────────────────────


class TestRegistryFunctions:

    def test_all_canonical_tools_returns_five(self) -> None:
        tools = all_canonical_tools()
        assert len(tools) == 5

    def test_canonical_tool_names(self) -> None:
        names = canonical_tool_names()
        assert names == frozenset({
            "file_read", "list_directory", "shell_exec", "file_write", "audisor_scan",
        })

    def test_production_tool_definitions_returns_list(self) -> None:
        defs = production_tool_definitions()
        assert isinstance(defs, list)
        assert len(defs) == 5
        names = {d["name"] for d in defs}
        assert names == canonical_tool_names()
