"""Unit tests for the policy engine port and shipped implementations."""

import pytest
from fastapi import HTTPException

from agent_server.auth.policy import (
    CompositePolicyEngine,
    LocalPolicyEngine,
    PolicyEngine,
    configure_policy_engine,
    get_policy_engine,
)
from agent_server.domain.policy import (
    DELETE,
    READ,
    SEARCH,
    AccessFilter,
    Permission,
    ResourceRef,
    ResourceType,
)
from agent_server.domain.user import User

# The platform defines no resource families — tests declare their own.
SKILL = ResourceType("skill")


def _ref(name: str = "my-skill", owner_id: str = "alice") -> ResourceRef:
    return ResourceRef(SKILL, name, owner_id=owner_id)


@pytest.fixture(autouse=True)
def _reset_engine():
    yield
    configure_policy_engine(LocalPolicyEngine())


# --- LocalPolicyEngine.check / require --------------------------------------


async def test_owner_is_allowed() -> None:
    engine = LocalPolicyEngine()
    assert await engine.check(User(identity="alice"), DELETE, _ref())


async def test_admin_permission_is_allowed_on_others_resources() -> None:
    engine = LocalPolicyEngine()
    assert await engine.check(User(identity="root", permissions=["admin"]), DELETE, _ref())


async def test_stranger_is_denied() -> None:
    engine = LocalPolicyEngine()
    assert not await engine.check(User(identity="bob"), DELETE, _ref())


async def test_require_raises_403_for_stranger() -> None:
    engine = LocalPolicyEngine()
    with pytest.raises(HTTPException) as exc:
        await engine.require(User(identity="bob"), DELETE, _ref())
    assert exc.value.status_code == 403


# --- LocalPolicyEngine.access_filter ----------------------------------------


async def test_access_filter_narrows_to_owner_for_regular_users() -> None:
    engine = LocalPolicyEngine()
    visible = await engine.access_filter(User(identity="alice"), SEARCH, SKILL)
    assert visible == AccessFilter(owner_id="alice")


async def test_access_filter_allows_all_for_admin() -> None:
    engine = LocalPolicyEngine()
    visible = await engine.access_filter(User(identity="root", permissions=["admin"]), SEARCH, SKILL)
    assert visible == AccessFilter(allow_all=True)


# --- CompositePolicyEngine ---------------------------------------------------


class _StubEngine(PolicyEngine):
    def __init__(self, *, allow: bool, visible: AccessFilter) -> None:
        self._allow = allow
        self._visible = visible

    async def check(
        self, subject: User, permission: Permission, resource: ResourceRef, context: dict | None = None
    ) -> bool:
        return self._allow

    async def require(
        self, subject: User, permission: Permission, resource: ResourceRef, context: dict | None = None
    ) -> None:
        if not self._allow:
            raise HTTPException(status_code=403)

    async def access_filter(
        self, subject: User, permission: Permission, resource_type: ResourceType, context: dict | None = None
    ) -> AccessFilter:
        return self._visible


async def test_composite_denies_when_any_engine_denies() -> None:
    composite = CompositePolicyEngine(
        [_StubEngine(allow=True, visible=AccessFilter(allow_all=True)), LocalPolicyEngine()]
    )
    assert await composite.check(User(identity="alice"), READ, _ref())
    assert not await composite.check(User(identity="bob"), READ, _ref())


async def test_composite_intersects_object_id_filters() -> None:
    a = _StubEngine(allow=True, visible=AccessFilter(object_ids=frozenset({"a", "b"})))
    b = _StubEngine(allow=True, visible=AccessFilter(object_ids=frozenset({"b", "c"})))
    composite = CompositePolicyEngine([a, b])
    assert (await composite.access_filter(User(identity="x"), SEARCH, SKILL)) == AccessFilter(
        object_ids=frozenset({"b"})
    )


def test_composite_rejects_empty_engine_list() -> None:
    with pytest.raises(ValueError, match="at least one"):
        CompositePolicyEngine([])


async def test_configure_policy_engine_swaps_the_provider_result() -> None:
    deny_all = _StubEngine(allow=False, visible=AccessFilter(object_ids=frozenset()))
    configure_policy_engine(deny_all)
    assert not await get_policy_engine().check(User(identity="alice"), READ, _ref())
