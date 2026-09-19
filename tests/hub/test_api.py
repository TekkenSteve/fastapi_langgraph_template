"""Integration tests for the hub REST API (thin-router contract).

The services are replaced at their providers — HTTP-level concerns (status
codes, request decoding, response shape, credential masking) are what these
tests pin. Service logic is covered by unit tests and the real-DB test.
"""

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from hub.models import McpConnectionView, SkillDetail, SkillSummary
from tests.fixtures.clients import create_test_app, make_client

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _summary(name: str = "my-skill") -> SkillSummary:
    return SkillSummary(name=name, description="d", created_at=_NOW, updated_at=_NOW)


def _detail(name: str = "my-skill") -> SkillDetail:
    return SkillDetail(
        **_summary(name).model_dump(),
        files=[{"path": "SKILL.md", "content": "---\nname: my-skill\ndescription: d\n---\n"}],
    )


@pytest.fixture
def service() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def client(service: AsyncMock) -> Iterator[TestClient]:
    app = create_test_app(include_runs=False, include_threads=False)
    from hub import api as hub_api

    app.include_router(hub_api.router)
    app.dependency_overrides[hub_api.get_skill_service] = lambda: service
    yield make_client(app)
    app.dependency_overrides.clear()


@pytest.fixture
def payload() -> dict[str, Any]:
    return {"files": [{"path": "SKILL.md", "content": "---\nname: my-skill\ndescription: d\n---\n# body\n"}]}


def test_list_returns_summaries(client: TestClient, service: AsyncMock) -> None:
    service.list_mine.return_value = [_summary()]
    response = client.get("/skills")
    assert response.status_code == 200
    assert response.json()[0]["name"] == "my-skill"
    assert response.json()[0]["tier"] == "user"


def test_install_returns_201_with_detail(client: TestClient, service: AsyncMock, payload: dict) -> None:
    service.install.return_value = _detail()
    response = client.post("/skills", json=payload)
    assert response.status_code == 201
    assert response.json()["files"][0]["path"] == "SKILL.md"
    service.install.assert_awaited_once()


def test_install_maps_validation_error_to_422(client: TestClient, service: AsyncMock) -> None:
    service.install.side_effect = HTTPException(status_code=422, detail="bad skill")
    response = client.post("/skills", json={"files": [{"path": "SKILL.md", "content": "junk"}]})
    assert response.status_code == 422


def test_get_returns_404_for_unknown_skill(client: TestClient, service: AsyncMock) -> None:
    service.get.side_effect = HTTPException(status_code=404, detail="not found")
    assert client.get("/skills/nope").status_code == 404


def test_replace_uses_put(client: TestClient, service: AsyncMock, payload: dict) -> None:
    service.replace.return_value = _detail()
    response = client.put("/skills/my-skill", json=payload)
    assert response.status_code == 200
    assert service.replace.call_args.args[0] == "my-skill"


def test_import_posts_url(client: TestClient, service: AsyncMock) -> None:
    service.import_from_url.return_value = _detail("imported")
    response = client.post("/skills/import", json={"url": "https://example.com/s/SKILL.md"})
    assert response.status_code == 201
    service.import_from_url.assert_awaited_once_with("https://example.com/s/SKILL.md")


def test_delete_returns_204(client: TestClient, service: AsyncMock) -> None:
    service.uninstall.return_value = None
    assert client.delete("/skills/my-skill").status_code == 204


SECRET = "super-secret-token"  # noqa: S105 — test fixture, must never leak into responses


def _view(name: str = "acme-kb") -> McpConnectionView:
    return McpConnectionView(
        name=name,
        transport="streamable_http",
        url="https://mcp.example.com/kb",
        auth_type="headers",
        header_keys=["Authorization"],
        enabled=True,
        created_at=_NOW,
        updated_at=_NOW,
    )


@pytest.fixture
def conn_service() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def conn_client(conn_service: AsyncMock) -> Iterator[TestClient]:
    app = create_test_app(include_runs=False, include_threads=False)
    from hub import api as hub_api

    app.include_router(hub_api.router)
    app.dependency_overrides[hub_api.get_mcp_connection_service] = lambda: conn_service
    yield make_client(app)
    app.dependency_overrides.clear()


def test_create_returns_201_and_never_echoes_credentials(conn_client: TestClient, conn_service: AsyncMock) -> None:
    conn_service.create.return_value = _view()
    response = conn_client.post(
        "/mcp-connections",
        json={
            "name": "acme-kb",
            "url": "https://mcp.example.com/kb",
            "auth_type": "headers",
            "headers": {"Authorization": SECRET},
        },
    )
    assert response.status_code == 201
    assert response.json()["header_keys"] == ["Authorization"]
    assert SECRET not in response.text


def test_stdio_transport_is_a_422(conn_client: TestClient) -> None:
    response = conn_client.post("/mcp-connections", json={"name": "ok", "transport": "stdio", "url": "https://x.com"})
    assert response.status_code == 422


def test_list_and_get(conn_client: TestClient, conn_service: AsyncMock) -> None:
    conn_service.list_mine.return_value = [_view()]
    conn_service.get.return_value = _view()
    assert conn_client.get("/mcp-connections").json()[0]["name"] == "acme-kb"
    assert conn_client.get("/mcp-connections/acme-kb").json()["transport"] == "streamable_http"


def test_get_returns_404_for_unknown(conn_client: TestClient, conn_service: AsyncMock) -> None:
    conn_service.get.side_effect = HTTPException(status_code=404)
    assert conn_client.get("/mcp-connections/nope").status_code == 404


def test_patch_and_delete(conn_client: TestClient, conn_service: AsyncMock) -> None:
    conn_service.update.return_value = _view()
    conn_service.delete.return_value = None
    assert conn_client.patch("/mcp-connections/acme-kb", json={"enabled": False}).status_code == 200
    assert conn_service.update.call_args.args[0] == "acme-kb"
    assert conn_client.delete("/mcp-connections/acme-kb").status_code == 204


# --- oauth callback (public route) --------------------------------------------


@pytest.fixture
def callback_client() -> Iterator[TestClient]:
    app = create_test_app(include_runs=False, include_threads=False)
    from hub import api as hub_api

    app.include_router(hub_api.public_router)
    yield make_client(app)
    app.dependency_overrides.clear()


def test_oauth_callback_parks_code(callback_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    completed = AsyncMock(return_value=True)
    monkeypatch.setattr("hub.api.complete_from_browser", completed)
    response = callback_client.get("/hub/oauth/callback", params={"code": "abc", "state": "st-1"})
    assert response.status_code == 200
    completed.assert_awaited_once_with("st-1", "abc")


def test_oauth_callback_rejects_unknown_state(callback_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("hub.api.complete_from_browser", AsyncMock(return_value=False))
    response = callback_client.get("/hub/oauth/callback", params={"code": "abc", "state": "stale"})
    assert response.status_code == 400
