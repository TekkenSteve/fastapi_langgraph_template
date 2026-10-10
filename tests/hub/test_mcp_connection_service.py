"""Unit tests for McpConnectionService: orchestration over repository + policy."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from agent_server.auth.policy import LocalPolicyEngine, PolicyEngine
from agent_server.auth.tool_authz import MCP_TOOL
from agent_server.config.settings import settings
from agent_server.domain.policy import AccessFilter, Permission, ResourceRef, ResourceType
from agent_server.domain.user import User
from hub.models import McpConnectionCreate, McpConnectionUpdate
from hub.services import McpConnectionService

NOW = datetime(2026, 1, 1, tzinfo=UTC)
SECRET = "s3cret"  # noqa: S105 — fixture; must never leak into views
# A public IP literal: the URL gate must not need DNS to accept a normal
# connection, so tests that are not about the gate stay offline.
PUBLIC_URL = "https://93.184.216.34/kb"


def _row(owner: str, name: str, **overrides: Any) -> SimpleNamespace:
    defaults: dict[str, Any] = {
        "user_id": owner,
        "name": name,
        "transport": "streamable_http",
        "url": PUBLIC_URL,
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


@pytest.fixture(autouse=True)
def _purge_oauth_state(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Deleting a connection purges OAuth state; the suite must stay DB-free.

    Only ``test_delete_purges_oauth_state`` asserts on it — the rest of the
    tests just need the call to be inert.
    """
    purge = AsyncMock()
    monkeypatch.setattr("hub.services.purge_oauth_state", purge)
    return purge


def _create(name: str = "acme-kb", url: str = PUBLIC_URL, headers: dict[str, str] | None = None) -> McpConnectionCreate:
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
    await service.create(_create(url="https://mcp.example.com/kb"))  # allowed host passes
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


class TestSsrfGate:
    """A connection URL is fetched server-side, so it gets the same public-address
    gate as skill import. All cases use IP literals, so no DNS is involved."""

    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1:8080/mcp",
            "http://169.254.169.254/latest/meta-data",
            "http://[::1]/mcp",
            "http://10.0.0.5/mcp",
            "http://192.168.1.10:9000/mcp",
            "http://100.64.0.7/mcp",  # CGNAT
        ],
    )
    async def test_private_and_metadata_targets_are_refused(self, url: str) -> None:
        with pytest.raises(HTTPException) as exc:
            await _service(FakeConnectionRepo()).create(_create(url=url))
        assert exc.value.status_code == 422

    async def test_public_target_is_accepted(self) -> None:
        view = await _service(FakeConnectionRepo()).create(_create(url=PUBLIC_URL))
        assert view.url == PUBLIC_URL

    async def test_update_revalidates_the_new_url_through_the_gate(self) -> None:
        repo = FakeConnectionRepo()
        service = _service(repo)
        await service.create(_create())
        with pytest.raises(HTTPException) as exc:
            await service.update("acme-kb", McpConnectionUpdate(url="http://169.254.169.254/mcp"))
        assert exc.value.status_code == 422

    async def test_the_allowlist_is_the_explicit_private_host_escape(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A self-hoster can vouch for one LAN host without opening everything."""
        from hub import services as svc_module

        monkeypatch.setattr(svc_module.hub_settings, "MCP_USER_ALLOWED_DOMAINS", "192.168.1.10")
        view = await _service(FakeConnectionRepo()).create(_create(url="http://192.168.1.10:9000/mcp"))
        assert view.url == "http://192.168.1.10:9000/mcp"

    async def test_an_empty_allowlist_does_not_excuse_a_private_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from hub import services as svc_module

        monkeypatch.setattr(svc_module.hub_settings, "MCP_USER_ALLOWED_DOMAINS", "")
        with pytest.raises(HTTPException) as exc:
            await _service(FakeConnectionRepo()).create(_create(url="http://10.1.2.3/mcp"))
        assert exc.value.status_code == 422


def _seeded(owner: str = "alice", name: str = "acme-kb", **overrides: Any) -> FakeConnectionRepo:
    repo = FakeConnectionRepo()
    repo.rows[(owner, name)] = _row(owner, name, **overrides)
    return repo


class TestUpdateAuthTypeHeaders:
    """``auth_type`` and ``headers`` are one pair, validated as the resulting state."""

    async def test_switch_to_oauth_clears_the_headers(self) -> None:
        repo = FakeConnectionRepo()
        service = _service(repo)
        await service.create(_create(headers={"Authorization": SECRET}))

        view = await service.update("acme-kb", McpConnectionUpdate(auth_type="oauth", headers={}))

        assert view.auth_type == "oauth"
        assert view.header_keys == []

    async def test_switch_to_headers_without_headers_is_422(self) -> None:
        repo = FakeConnectionRepo()
        service = _service(repo)
        await service.create(_create())

        with pytest.raises(HTTPException) as exc:
            await service.update("acme-kb", McpConnectionUpdate(auth_type="headers"))
        assert exc.value.status_code == 422

    async def test_leaving_credentials_on_a_non_header_auth_type_is_422(self) -> None:
        """Otherwise the row holds a secret nothing will ever send."""
        repo = FakeConnectionRepo()
        service = _service(repo)
        await service.create(_create(headers={"Authorization": SECRET}))

        with pytest.raises(HTTPException) as exc:
            await service.update("acme-kb", McpConnectionUpdate(auth_type="oauth"))
        assert exc.value.status_code == 422


async def test_delete_purges_oauth_state(_purge_oauth_state: AsyncMock) -> None:
    """Recreating the same name must not inherit tokens or a DCR registration."""
    repo = FakeConnectionRepo()
    service = _service(repo)
    await service.create(_create())

    await service.delete("acme-kb")

    _purge_oauth_state.assert_awaited_once_with("alice", "acme-kb")


class DenyToolEngine(LocalPolicyEngine):
    """Allows everything the local engine allows except per-tool authorization."""

    async def require(
        self, subject: User, permission: Permission, resource: ResourceRef, context: dict | None = None
    ) -> None:
        if resource.type == MCP_TOOL:
            raise HTTPException(status_code=403, detail="tool denied")
        await super().require(subject, permission, resource, context)


class TestHostProxy:
    """Proxy calls go through the service: ownership, policy, enabled, breaker."""

    async def test_stranger_gets_404(self) -> None:
        repo = _seeded()
        with pytest.raises(HTTPException) as exc:
            await _service(repo, user=User(identity="bob")).proxy_list_tools("acme-kb")
        assert exc.value.status_code == 404

    async def test_policy_denial_is_403(self) -> None:
        with pytest.raises(HTTPException) as exc:
            await _service(_seeded(), policy=DenyAllEngine()).proxy_list_tools("acme-kb")
        assert exc.value.status_code == 403

    async def test_disabled_connection_is_409(self) -> None:
        with pytest.raises(HTTPException) as exc:
            await _service(_seeded(enabled=False)).proxy_list_tools("acme-kb")
        assert exc.value.status_code == 409

    async def test_list_tools_delegates_with_the_resolved_row(self, monkeypatch: pytest.MonkeyPatch) -> None:
        row = _row("alice", "acme-kb")
        repo = _seeded()
        listing = AsyncMock(return_value=[{"name": "render"}])
        monkeypatch.setattr("hub.services.apps_host.list_tools", listing)

        assert await _service(repo).proxy_list_tools("acme-kb") == [{"name": "render"}]
        listing.assert_awaited_once_with(row)

    async def test_call_tool_skips_the_tool_check_when_authz_is_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        call = AsyncMock(return_value={"content": [], "artifact": None})
        monkeypatch.setattr("hub.services.apps_host.call_tool", call)
        monkeypatch.setattr(settings.mcp, "MCP_TOOL_AUTHZ_ENABLED", False)

        await _service(_seeded(), policy=DenyToolEngine()).proxy_call_tool("acme-kb", "render", {})

        call.assert_awaited_once()

    async def test_call_tool_checks_per_invocation_authz_when_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Same switch and vocabulary as the model path's PolicyToolInterceptor."""
        call = AsyncMock()
        monkeypatch.setattr("hub.services.apps_host.call_tool", call)
        monkeypatch.setattr(settings.mcp, "MCP_TOOL_AUTHZ_ENABLED", True)

        with pytest.raises(HTTPException) as exc:
            await _service(_seeded(), policy=DenyToolEngine()).proxy_call_tool("acme-kb", "render", {})

        assert exc.value.status_code == 403
        call.assert_not_awaited()
