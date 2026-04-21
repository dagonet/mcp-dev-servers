"""Import-and-registration smoke tests for all 6 MCP servers."""

import importlib

import pytest

SERVERS = [
    ("mcp_dev_servers.git_mcp", 19),
    ("mcp_dev_servers.github_mcp", 2),
    ("mcp_dev_servers.dotnet_mcp", 19),
    ("mcp_dev_servers.ollama_mcp", 6),
    ("mcp_dev_servers.rust_mcp", 4),
    ("mcp_dev_servers.template_sync_mcp", 8),
]


@pytest.mark.parametrize("module_name,expected_tool_count", SERVERS)
def test_server_imports_and_registers_tools(module_name, expected_tool_count):
    module = importlib.import_module(module_name)

    assert callable(getattr(module, "main", None)), (
        f"{module_name} must expose a callable `main` for the console-script entry point"
    )

    mcp = getattr(module, "mcp", None)
    assert mcp is not None, f"{module_name} must expose its FastMCP instance as `mcp`"

    tools = mcp._tool_manager.list_tools()
    assert len(tools) == expected_tool_count, (
        f"{module_name} registered {len(tools)} tools, expected {expected_tool_count}"
    )
