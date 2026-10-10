"""Unit tests for the audit ledger query route (admin only)."""

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from agent_server.auth.deps import get_current_user, require_auth
from agent_server.domain.user import User
from tests.fixtures.clients import create_test_app, make_client
from tests.fixtures.session_fixtures import BasicSession, override_session_dependency

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _row(**overrides: Any) -> Any:
    defaults: dict[str, Any] = {
        "entry_id": 7,
        "user_id": "u1",
        "thread_id": "t1",
        "run_id": "r1",
        "action": "tool_call",
        "resource": "web_search",
        "status": "ok",
        "duration_ms": 12,
        "detail": {"message": "args={}"},
        "created_at": _NOW,
    }
    defaults.update(overrides)
    return type("Row", (), defaults)()


@pytest.fixture
def entries(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    from agent_server.controller.http.routers import audit as audit_router

    reader = AsyncMock(return_value=[_row()])
    monkeypatch.setattr(audit_router, "list_audit_entries", reader)
    return reader


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_test_app(include_runs=False, include_threads=False)
    from agent_server.controller.http.routers.audit import router as audit_router

    app.include_router(audit_router)
    override_session_dependency(app, BasicSession)
    yield make_client(app)
    app.dependency_overrides.clear()


def _as_admin(app_client: TestClient) -> None:
    """LocalPolicyEngine allows the ``admin`` permission through ownerless checks."""
    app = app_client.app
    admin = User(identity="admin-user", permissions=["admin"])
    app.dependency_overrides[get_current_user] = lambda: admin
    app.dependency_overrides[require_auth] = lambda: admin


def test_a_regular_user_cannot_read_the_ledger(client: TestClient, entries: AsyncMock) -> None:
    """The ledger spans tenants; the default engine denies its ownerless resource."""
    response = client.get("/audit/logs")

    assert response.status_code == 403
    entries.assert_not_awaited()


def test_an_admin_gets_entries_newest_first(client: TestClient, entries: AsyncMock) -> None:
    _as_admin(client)

    response = client.get("/audit/logs")

    assert response.status_code == 200
    assert response.json()[0]["action"] == "tool_call"
    assert response.json()[0]["detail"] == {"message": "args={}"}


def test_filters_and_cursor_reach_the_query(client: TestClient, entries: AsyncMock) -> None:
    _as_admin(client)

    response = client.get("/audit/logs?user_id=u1&thread_id=t1&run_id=r1&action=tool_call&before=9&limit=5")

    assert response.status_code == 200
    entries.assert_awaited_once()
    kwargs = entries.await_args.kwargs
    assert kwargs["user_id"] == "u1"
    assert kwargs["thread_id"] == "t1"
    assert kwargs["run_id"] == "r1"
    assert kwargs["action"] == "tool_call"
    assert kwargs["before"] == 9
    assert kwargs["limit"] == 5


@pytest.mark.parametrize("query", ["limit=0", "limit=501", "before=not-a-number"])
def test_out_of_range_paging_is_422(client: TestClient, query: str) -> None:
    _as_admin(client)

    assert client.get(f"/audit/logs?{query}").status_code == 422
