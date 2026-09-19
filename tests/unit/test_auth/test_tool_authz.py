"""Unit tests for per-invocation MCP tool authorization (auth/tool_authz.py)."""

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from agent_server.auth.policy import LocalPolicyEngine
from agent_server.auth.tool_authz import PolicyToolInterceptor
from agent_server.domain.policy import Permission, ResourceRef
from agent_server.domain.user import User


class _RecordingEngine(LocalPolicyEngine):
    def __init__(self, *, allow: bool) -> None:
        self._allow = allow
        self.calls: list[tuple[str, str, str]] = []

    async def check(
        self, subject: User, permission: Permission, resource: ResourceRef, context: dict | None = None
    ) -> bool:
        self.calls.append((subject.identity, str(permission), resource.id))
        return self._allow


class _NeverCalled:
    """Handler placeholder for deny-path tests (must not be awaited)."""

    async def __call__(self, request: Any) -> Any:
        raise AssertionError("handler must not run")


def _request(server: str = "acme-kb", tool: str = "search") -> Any:
    return SimpleNamespace(server_name=server, name=tool, args={})


async def test_allowed_call_reaches_handler() -> None:
    engine = _RecordingEngine(allow=True)
    interceptor = PolicyToolInterceptor(engine, "alice")

    async def handler(request: Any) -> str:
        return "done"

    result = await interceptor(_request(), handler)
    assert result == "done"
    assert engine.calls == [("alice", "execute", "acme-kb/search")]


async def test_denied_call_never_reaches_handler() -> None:
    interceptor = PolicyToolInterceptor(_RecordingEngine(allow=False), "bob")
    with pytest.raises(HTTPException) as exc:
        await interceptor(_request(), _NeverCalled())
    assert exc.value.status_code == 403


async def test_local_engine_denies_ownerless_tools() -> None:
    """Fail-closed by construction: tools have no owner and the interceptor's
    subject carries identity only (no permissions), so the local engine denies
    everyone — enabling tool authz requires a real policy backend."""
    interceptor = PolicyToolInterceptor(LocalPolicyEngine(), "alice")
    with pytest.raises(HTTPException):
        await interceptor(_request(), _NeverCalled())
