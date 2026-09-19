"""Integration tests for the MCP Apps host proxy routes (helpers mocked)."""

from collections.abc import Iterator
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from hub import apps_host
from tests.fixtures.clients import create_test_app, make_client
from tests.fixtures.session_fixtures import BasicSession, override_session_dependency


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_test_app(include_runs=False, include_threads=False)
    from hub import api as hub_api

    app.include_router(hub_api.router)
    override_session_dependency(app, BasicSession)
    yield make_client(app)
    app.dependency_overrides.clear()


def test_tools_list_passes_through(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        apps_host,
        "list_tools",
        AsyncMock(return_value=[{"name": "render", "description": "d", "metadata": {"_meta": {"ui": {}}}}]),
    )
    response = client.post("/hub/mcp/acme-kb/tools/list")
    assert response.status_code == 200
    assert response.json()[0]["name"] == "render"


def test_tools_call_passes_content_and_artifact(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    call = AsyncMock(
        return_value={"content": [{"type": "text", "text": "ok"}], "artifact": {"structured_content": {"x": 1}}}
    )
    monkeypatch.setattr(apps_host, "call_tool", call)
    response = client.post("/hub/mcp/acme-kb/tools/call", json={"tool_name": "render", "args": {"a": 1}})
    assert response.status_code == 200
    assert response.json()["artifact"]["structured_content"] == {"x": 1}
    assert call.call_args.args[1:] == ("test-user", "acme-kb", "render", {"a": 1})


def test_resources_list_and_read(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        apps_host, "list_resources", AsyncMock(return_value={"resources": [], "resource_templates": []})
    )
    monkeypatch.setattr(
        apps_host, "read_resource", AsyncMock(return_value={"contents": [{"uri": "ui://x", "text": "<html/>"}]})
    )
    assert client.post("/hub/mcp/acme-kb/resources/list").status_code == 200
    response = client.post("/hub/mcp/acme-kb/resources/read", json={"uri": "ui://x"})
    assert response.json()["contents"][0]["text"] == "<html/>"


def test_unknown_connection_is_404(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        apps_host, "list_tools", AsyncMock(side_effect=HTTPException(status_code=404, detail="not found"))
    )
    assert client.post("/hub/mcp/nope/tools/list").status_code == 404


def test_unreachable_server_is_502(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        apps_host, "list_tools", AsyncMock(side_effect=HTTPException(status_code=502, detail="unreachable"))
    )
    assert client.post("/hub/mcp/acme-kb/tools/list").status_code == 502
