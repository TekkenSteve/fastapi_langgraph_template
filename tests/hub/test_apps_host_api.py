"""Integration tests for the MCP Apps host proxy routes (service mocked).

The router is thin: it decodes, delegates to ``McpConnectionService.proxy_*``
and encodes. Resolution, authorization and session handling live in the
service (covered by ``test_mcp_connection_service.py``) and in ``apps_host``
(covered by ``test_apps_host.py``), so this file pins the HTTP contract only.
"""

from collections.abc import Iterator
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from tests.fixtures.clients import create_test_app, make_client


@pytest.fixture
def service() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def client(service: AsyncMock) -> Iterator[TestClient]:
    app = create_test_app(include_runs=False, include_threads=False)
    from hub import api as hub_api

    app.include_router(hub_api.router)
    app.dependency_overrides[hub_api.get_mcp_connection_service] = lambda: service
    yield make_client(app)
    app.dependency_overrides.clear()


def test_tools_list_passes_through(client: TestClient, service: AsyncMock) -> None:
    service.proxy_list_tools.return_value = [{"name": "render", "description": "d", "metadata": {"_meta": {"ui": {}}}}]
    response = client.post("/hub/mcp/acme-kb/tools/list")
    assert response.status_code == 200
    assert response.json()[0]["name"] == "render"
    service.proxy_list_tools.assert_awaited_once_with("acme-kb")


def test_tools_call_passes_content_and_artifact(client: TestClient, service: AsyncMock) -> None:
    service.proxy_call_tool.return_value = {
        "content": [{"type": "text", "text": "ok"}],
        "artifact": {"structured_content": {"x": 1}},
    }
    response = client.post("/hub/mcp/acme-kb/tools/call", json={"tool_name": "render", "args": {"a": 1}})
    assert response.status_code == 200
    assert response.json()["artifact"]["structured_content"] == {"x": 1}
    service.proxy_call_tool.assert_awaited_once_with("acme-kb", "render", {"a": 1})


def test_resources_list_and_read(client: TestClient, service: AsyncMock) -> None:
    service.proxy_list_resources.return_value = {"resources": [], "resource_templates": []}
    service.proxy_read_resource.return_value = {"contents": [{"uri": "ui://x", "text": "<html/>"}]}

    assert client.post("/hub/mcp/acme-kb/resources/list").status_code == 200
    response = client.post("/hub/mcp/acme-kb/resources/read", json={"uri": "ui://x"})

    assert response.json()["contents"][0]["text"] == "<html/>"
    service.proxy_read_resource.assert_awaited_once_with("acme-kb", "ui://x")


def test_unknown_connection_is_404(client: TestClient, service: AsyncMock) -> None:
    service.proxy_list_tools.side_effect = HTTPException(status_code=404, detail="not found")
    assert client.post("/hub/mcp/nope/tools/list").status_code == 404


def test_denied_by_policy_is_403(client: TestClient, service: AsyncMock) -> None:
    service.proxy_call_tool.side_effect = HTTPException(status_code=403, detail="denied")
    response = client.post("/hub/mcp/acme-kb/tools/call", json={"tool_name": "render"})
    assert response.status_code == 403


def test_disabled_connection_is_409(client: TestClient, service: AsyncMock) -> None:
    service.proxy_list_tools.side_effect = HTTPException(status_code=409, detail="disabled")
    assert client.post("/hub/mcp/acme-kb/tools/list").status_code == 409


def test_unreachable_server_is_502(client: TestClient, service: AsyncMock) -> None:
    service.proxy_list_tools.side_effect = HTTPException(status_code=502, detail="unreachable")
    assert client.post("/hub/mcp/acme-kb/tools/list").status_code == 502
