"""Unit tests for MCP Apps client-side support (capability + visibility)."""

from typing import Any

from langchain_core.tools import BaseTool
from mcp import types

from agent_server.repo.graphs.mcp_apps import (
    MCP_APPS_EXTENSION_ID,
    MCP_APPS_MIME_TYPE,
    filter_model_facing_tools,
    get_tool_ui_meta,
    inject_ui_extension_into_request,
    is_model_facing,
)


def _initialize_request() -> types.ClientRequest:
    params = types.InitializeRequestParams(
        protocolVersion=types.LATEST_PROTOCOL_VERSION,
        capabilities=types.ClientCapabilities(),
        clientInfo=types.Implementation(name="test", version="0.0.1"),
    )
    return types.ClientRequest(types.InitializeRequest(method="initialize", params=params))


def _tool(name: str, metadata: dict[str, Any] | None = None) -> BaseTool:
    class _T(BaseTool):
        def _run(self, **kwargs: Any) -> Any:
            raise NotImplementedError

        async def _arun(self, **kwargs: Any) -> Any:
            return kwargs

    return _T(name=name, description=f"{name} tool", metadata=metadata)


def test_inject_adds_ui_extension_to_initialize() -> None:
    injected = inject_ui_extension_into_request(_initialize_request())
    extensions = injected.root.params.capabilities.model_dump()["extensions"]
    assert extensions[MCP_APPS_EXTENSION_ID] == {"mimeTypes": [MCP_APPS_MIME_TYPE]}


def test_inject_is_idempotent() -> None:
    once = inject_ui_extension_into_request(_initialize_request())
    assert inject_ui_extension_into_request(once) is once


def test_inject_ignores_non_initialize_requests() -> None:
    request = types.ClientRequest(types.ListToolsRequest(method="tools/list"))
    assert inject_ui_extension_into_request(request) is request


def test_tools_without_ui_meta_are_model_facing() -> None:
    assert is_model_facing(_tool("plain"))
    assert get_tool_ui_meta(_tool("plain")) == {}


def test_app_only_tool_is_filtered_out() -> None:
    model_tool = _tool("search", {"_meta": {"ui": {"visibility": ["model", "app"], "resourceUri": "ui://x"}}})
    app_only = _tool("render", {"_meta": {"ui": {"visibility": ["app"], "resourceUri": "ui://y"}}})
    filtered = filter_model_facing_tools([model_tool, app_only])
    assert [t.name for t in filtered] == ["search"]
    assert get_tool_ui_meta(app_only)["resourceUri"] == "ui://y"


def test_legacy_flat_ui_meta_form_is_tolerated() -> None:
    tool = _tool("legacy", {"_meta": {"ui/resourceUri": "ui://old"}})
    assert get_tool_ui_meta(tool) == {"resourceUri": "ui://old"}
