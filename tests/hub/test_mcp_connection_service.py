"""Unit tests for McpConnectionService: orchestration over repository + policy."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from agent_server.auth.policy import LocalPolicyEngine, PolicyEngine
from agent_server.domain.policy import AccessFilter, Permission, ResourceRef, ResourceType
from agent_server.domain.user import User
from hub.models import McpConnectionCreate, McpConnectionUpdate
from hub.services import McpConnectionService

NOW = datetime(2026, 1, 1, tzinfo=UTC)
SECRET = "s3cret"  # noqa: S105 — fixture; must never leak into views


def _row(owner: str, name: str, **overrides: Any) -> SimpleNamespace:
    defaults: dict[str, Any] = {
        "user_id": owner,
        "name": name,
        "transport": "streamable_http",
        "url": "https://mcp.example.com/kb",
        "auth_type": "none",
        "headers": {},
        "enabled": True,
        "created_at": NOW,
        "updated_at": NOW,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class FakeConnectionRepo:
    """In-memory McpConnectionRepository."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], Any] = {}

    async def list_for_owner(self, owner_id: str) -> list[Any]:
        return sorted((r for (o, _), r in self.rows.items() if o == owner_id), key=lambda r: r.name)

    async def get_for_owner(self, owner_id: str, name: str) -> Any | None:
        return self.rows.get((owner_id, name))

    async def insert(
        self, owner_id: str, name: str, *, transport: str, url: str, auth_type: str, headers: dict[str, str]
    ) -> Any:
        row = _row(owner_id, name, transport=transport, url=url, auth_type=auth_type, headers=headers)
        self.rows[(owner_id, name)] = row
        return row

    async def save(self, row: Any) -> None:
        row.updated_at = NOW

    async def delete(self, row: Any) -> None:
        self.rows.pop((row.user_id, row.name), None)


class DenyAllEngine(PolicyEngine):
    async def check(
        self, subject: User, permission: Permission, resource: ResourceRef, context: dict | None = None
    ) -> bool:
        return False

    async def require(
        self, subject: User, permission: Permission, resource: ResourceRef, context: dict | None = None
    ) -> None:
        raise HTTPException(status_code=403)

    async def access_filter(
        self, subject: User, permission: Permission, resource_type: ResourceType, context: dict | None = None
    ) -> AccessFilter:
        return AccessFilter(object_ids=frozenset())


def _service(
    repo: FakeConnectionRepo, user: User | None = None, policy: PolicyEngine | None = None
) -> McpConnectionService:
    return McpConnectionService(repo, policy or LocalPolicyEngine(), user or User(identity="alice"))


def _create(
    name: str = "acme-kb", url: str = "https://mcp.example.com/kb", headers: dict[str, str] | None = None
) -> McpConnectionCreate:
    return McpConnectionCreate(name=name, url=url, auth_type="headers" if headers else "none", headers=headers or {})


async def test_create_returns_masked_view() -> None:
    view = await _service(FakeConnectionRepo()).create(_create(headers={"Authorization": SECRET}))
    assert view.header_keys == ["Authorization"]
    assert SECRET not in view.model_dump_json()


async def test_create_rejects_bad_name_and_url() -> None:
    service = _service(FakeConnectionRepo())
    with pytest.raises(HTTPException) as exc:
        await service.create(_create(name="Bad_Name"))
    assert exc.value.status_code == 422
    with pytest.raises(HTTPException) as exc:
        await service.create(_create(url="file:///etc/passwd"))
    assert exc.value.status_code == 422


async def test_create_enforces_domain_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    # Patch via the service module's own settings reference, not a test-side
    # import — settings objects can diverge (module reloads in other tests).
    from hub import services as svc_module

    monkeypatch.setattr(svc_module.hub_settings, "MCP_USER_ALLOWED_DOMAINS", "mcp.example.com")
    service = _service(FakeConnectionRepo())
    await service.create(_create())  # allowed host passes
    with pytest.raises(HTTPException) as exc:
        await service.create(_create(name="other", url="https://evil.example.org/mcp"))
    assert exc.value.status_code == 422


async def test_create_rejects_duplicates_with_409() -> None:
    repo = FakeConnectionRepo()
    service = _service(repo)
    await service.create(_create())
    with pytest.raises(HTTPException) as exc:
        await service.create(_create())
    assert exc.value.status_code == 409


async def test_create_denied_by_policy_is_403() -> None:
    with pytest.raises(HTTPException) as exc:
        await _service(FakeConnectionRepo(), policy=DenyAllEngine()).create(_create())
    assert exc.value.status_code == 403


async def test_same_name_different_owner_is_not_a_collision() -> None:
    repo = FakeConnectionRepo()
    await _service(repo).create(_create())
    view = await _service(repo, user=User(identity="bob")).create(_create())
    assert view.name == "acme-kb"
    assert len(repo.rows) == 2


async def test_get_is_404_for_strangers() -> None:
    repo = FakeConnectionRepo()
    await _service(repo).create(_create())
    with pytest.raises(HTTPException) as exc:
        await _service(repo, user=User(identity="bob")).get("acme-kb")
    assert exc.value.status_code == 404


async def test_update_patches_fields_and_replaces_headers() -> None:
    repo = FakeConnectionRepo()
    service = _service(repo)
    await service.create(_create(headers={"Old": "1"}))
    view = await service.update("acme-kb", McpConnectionUpdate(enabled=False, headers={"New": "2"}))
    assert view.enabled is False
    assert view.header_keys == ["New"]


async def test_update_validates_new_url() -> None:
    repo = FakeConnectionRepo()
    service = _service(repo)
    await service.create(_create())
    with pytest.raises(HTTPException) as exc:
        await service.update("acme-kb", McpConnectionUpdate(url="not-a-url"))
    assert exc.value.status_code == 422


async def test_delete_removes() -> None:
    repo = FakeConnectionRepo()
    service = _service(repo)
    await service.create(_create())
    await service.delete("acme-kb")
    assert repo.rows == {}


async def test_delete_unknown_is_404() -> None:
    with pytest.raises(HTTPException) as exc:
        await _service(FakeConnectionRepo()).delete("nope")
    assert exc.value.status_code == 404
