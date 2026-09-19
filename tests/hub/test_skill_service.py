"""Unit tests for SkillService: orchestration over repository + policy ports.

Repositories and engines are fakes — SQL and auth mechanics are covered
elsewhere (real-DB integration test, policy unit tests).
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from agent_server.auth.policy import LocalPolicyEngine, PolicyEngine
from agent_server.domain.policy import AccessFilter, Permission, ResourceRef, ResourceType
from agent_server.domain.user import User
from hub.models import SkillInstall
from hub.services import SkillService

NOW = datetime(2026, 1, 1, tzinfo=UTC)
SKILL_MD = "---\nname: my-skill\ndescription: does things\nlicense: MIT\n---\n# Body\n"


def _payload(description: str = "does things") -> SkillInstall:
    return SkillInstall.model_validate(
        {
            "files": [
                {"path": "SKILL.md", "content": SKILL_MD.replace("does things", description)},
                {"path": "scripts/h.py", "content": "print(1)\n"},
            ]
        }
    )


def _row(owner: str, name: str) -> SimpleNamespace:
    return SimpleNamespace(
        skill_id=f"id-{owner}-{name}",
        user_id=owner,
        name=name,
        description="d",
        license=None,
        metadata_dict={},
        created_at=NOW,
        updated_at=NOW,
    )


class FakeSkillRepo:
    """In-memory SkillRepository."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], Any] = {}
        self.files: dict[str, list[Any]] = {}
        self.deleted: list[str] = []

    async def list_for_owner(self, owner_id: str) -> list[Any]:
        return sorted((r for (o, _), r in self.rows.items() if o == owner_id), key=lambda r: r.name)

    async def list_by_names(self, names: frozenset[str]) -> list[Any]:
        return [r for r in self.rows.values() if r.name in names]

    async def list_all(self) -> list[Any]:
        return list(self.rows.values())

    async def get_for_owner(self, owner_id: str, name: str) -> Any | None:
        return self.rows.get((owner_id, name))

    async def list_files(self, skill_id: str) -> list[Any]:
        return self.files.get(skill_id, [])

    async def save(
        self,
        owner_id: str,
        name: str,
        *,
        description: str,
        license: str | None,
        metadata: dict,
        files: dict[str, bytes],
    ) -> Any:
        row = self.rows.get((owner_id, name)) or _row(owner_id, name)
        row.description = description
        row.license = license
        row.metadata_dict = metadata
        row.updated_at = NOW
        self.rows[(owner_id, name)] = row
        self.files[row.skill_id] = [SimpleNamespace(path=p, content=c) for p, c in files.items()]
        return row

    async def delete(self, row: Any) -> None:
        self.rows.pop((row.user_id, row.name), None)
        self.deleted.append(row.name)


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


def _service(repo: FakeSkillRepo, user: User | None = None, policy: PolicyEngine | None = None) -> SkillService:
    return SkillService(repo, policy or LocalPolicyEngine(), user or User(identity="alice"))


async def test_install_creates_and_returns_files() -> None:
    repo = FakeSkillRepo()
    detail = await _service(repo).install(_payload())
    assert detail.name == "my-skill"
    assert detail.license == "MIT"
    assert {f.path for f in detail.files} == {"SKILL.md", "scripts/h.py"}
    assert ("alice", "my-skill") in repo.rows


async def test_install_replaces_same_named_skill() -> None:
    repo = FakeSkillRepo()
    service = _service(repo)
    await service.install(_payload())
    replaced = await service.install(_payload("v2"))
    assert replaced.description == "v2"
    assert len(repo.rows) == 1


async def test_install_maps_validation_error_to_422() -> None:
    with pytest.raises(HTTPException) as exc:
        await _service(FakeSkillRepo()).install(
            SkillInstall.model_validate({"files": [{"path": "SKILL.md", "content": "junk"}]})
        )
    assert exc.value.status_code == 422


async def test_install_denied_by_policy_is_403() -> None:
    with pytest.raises(HTTPException) as exc:
        await _service(FakeSkillRepo(), policy=DenyAllEngine()).install(_payload())
    assert exc.value.status_code == 403


async def test_get_returns_404_for_strangers_and_unknown() -> None:
    repo = FakeSkillRepo()
    await _service(repo).install(_payload())
    with pytest.raises(HTTPException) as exc:
        await _service(repo, user=User(identity="bob")).get("my-skill")
    assert exc.value.status_code == 404  # tenant isolation hides existence


async def test_get_returns_files() -> None:
    repo = FakeSkillRepo()
    await _service(repo).install(_payload())
    detail = await _service(repo).get("my-skill")
    assert {f.path for f in detail.files} == {"SKILL.md", "scripts/h.py"}


async def test_replace_rejects_name_mismatch() -> None:
    repo = FakeSkillRepo()
    await _service(repo).install(_payload())
    with pytest.raises(HTTPException) as exc:
        await _service(repo).replace("other-name", _payload())
    assert exc.value.status_code == 422


async def test_replace_rejects_absent_skill() -> None:
    with pytest.raises(HTTPException) as exc:
        await _service(FakeSkillRepo()).replace("my-skill", _payload())
    assert exc.value.status_code == 404


async def test_uninstall_deletes() -> None:
    repo = FakeSkillRepo()
    await _service(repo).install(_payload())
    await _service(repo).uninstall("my-skill")
    assert repo.deleted == ["my-skill"]
    assert repo.rows == {}


async def test_list_mine_uses_owner_filter_for_regular_users() -> None:
    repo = FakeSkillRepo()
    repo.rows[("alice", "mine")] = _row("alice", "mine")
    repo.rows[("bob", "theirs")] = _row("bob", "theirs")
    names = [s.name for s in await _service(repo).list_mine()]
    assert names == ["mine"]


async def test_list_mine_allows_all_for_admin() -> None:
    repo = FakeSkillRepo()
    repo.rows[("alice", "mine")] = _row("alice", "mine")
    repo.rows[("bob", "theirs")] = _row("bob", "theirs")
    admin = User(identity="root", permissions=["admin"])
    names = sorted(s.name for s in await _service(repo, user=admin).list_mine())
    assert names == ["mine", "theirs"]


async def test_import_from_url_installs_parsed_payload() -> None:
    async def fetcher(url: str, **kwargs: Any) -> bytes:
        return SKILL_MD.encode()

    detail = await _service(FakeSkillRepo()).import_from_url("https://x/skill.md", fetcher=fetcher)
    assert detail.name == "my-skill"


async def test_import_from_url_maps_bad_payload_to_422() -> None:
    async def fetcher(url: str, **kwargs: Any) -> bytes:
        return b"\x89PNG binary junk"

    with pytest.raises(HTTPException) as exc:
        await _service(FakeSkillRepo()).import_from_url("https://x/blob", fetcher=fetcher)
    assert exc.value.status_code == 422
