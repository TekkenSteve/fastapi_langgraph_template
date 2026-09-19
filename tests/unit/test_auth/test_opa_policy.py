"""Unit tests for the OPA policy engine binding (mocked HTTP transport)."""

import json

import httpx
import pytest
from fastapi import HTTPException

from agent_server.auth.opa_policy import OpaPolicyEngine
from agent_server.config.settings import settings
from agent_server.domain.policy import (
    DELETE,
    READ,
    SEARCH,
    UPDATE,
    AccessFilter,
    ResourceRef,
    ResourceType,
)
from agent_server.domain.user import User

SKILL = ResourceType("skill")


def _engine(result: object, *, status: int = 200) -> OpaPolicyEngine:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"result": result})

    return OpaPolicyEngine("http://opa:8181", package="hub/authz", transport=httpx.MockTransport(handler))


def _down_engine() -> OpaPolicyEngine:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    return OpaPolicyEngine("http://opa:8181", package="hub/authz", transport=httpx.MockTransport(handler))


_ALICE = User(identity="alice")
_BOB = User(identity="bob")
_REF = ResourceRef(SKILL, "my-skill", owner_id="alice")


async def test_allow_maps_to_true() -> None:
    assert await _engine(True).check(_ALICE, READ, _REF)


async def test_deny_maps_to_false_and_require_403s() -> None:
    engine = _engine(False)
    assert not await engine.check(_ALICE, READ, _REF)
    with pytest.raises(HTTPException) as exc:
        await engine.require(_BOB, DELETE, _REF)
    assert exc.value.status_code == 403


async def test_input_document_shape() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"result": True})

    engine = OpaPolicyEngine("http://opa:8181", package="hub/authz", transport=httpx.MockTransport(handler))
    await engine.check(User(identity="alice", permissions=["admin"]), UPDATE, _REF)
    assert captured["input"]["subject"] == {"identity": "alice", "permissions": ["admin"]}
    assert captured["input"]["permission"] == "update"
    assert captured["input"]["resource"] == {"type": "skill", "id": "my-skill", "owner_id": "alice"}


async def test_filter_maps_to_access_filter() -> None:
    engine = _engine({"allow_all": False, "owner_id": "alice", "object_ids": None})
    visible = await engine.access_filter(_ALICE, SEARCH, SKILL)
    assert visible == AccessFilter(owner_id="alice")


async def test_filter_maps_object_ids() -> None:
    engine = _engine({"allow_all": False, "owner_id": None, "object_ids": ["a", "b"]})
    visible = await engine.access_filter(_ALICE, SEARCH, SKILL)
    assert visible == AccessFilter(object_ids=frozenset({"a", "b"}))


async def test_opa_down_fails_closed_with_503(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.policy, "OPA_FAIL_CLOSED", True)
    with pytest.raises(HTTPException) as exc:
        await _down_engine().check(_ALICE, READ, _REF)
    assert exc.value.status_code == 503


async def test_opa_down_fail_open_uses_local_semantics(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.policy, "OPA_FAIL_CLOSED", False)
    engine = _down_engine()
    assert await engine.check(_ALICE, READ, _REF)  # owner
    assert not await engine.check(_BOB, READ, _REF)  # stranger
    visible = await engine.access_filter(_ALICE, SEARCH, SKILL)
    assert visible == AccessFilter(owner_id="alice")
