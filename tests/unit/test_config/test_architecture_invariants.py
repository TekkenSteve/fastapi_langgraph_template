"""Architecture invariants that used to live only in prose.

Each of these is a rule AGENTS.md states — two drivers that must not cross, two
migration chains that must not mix, stdio staying a deployment privilege. An ADR
would record them; a test enforces them. Where a rule can be expressed
structurally, that is what happens here, so a violation fails CI instead of
depending on someone remembering.
"""

import ast
from pathlib import Path

import pytest

from agent_server.config.settings import settings

_SRC = Path(__file__).resolve().parents[3] / "src"

# Modules allowed to touch the libpq (sync) URL: the store/checkpoint pool, the
# framework migration runner and the hub migration runner. Anything else
# reaching for it is the cross-driver bug this test exists to catch.
_SYNC_URL_ALLOWED = {
    "agent_server/config/settings",  # the definition site
    "agent_server/repo/database",
    "agent_server/repo/migrations",
    "hub/migrate",
}


def _python_files(*roots: str) -> list[Path]:
    return [p for root in roots for p in (_SRC / root).rglob("*.py")]


def _module_name(path: Path) -> str:
    return str(path.relative_to(_SRC)).removesuffix(".py")


# --- two drivers, one direction each ------------------------------------------


def test_the_two_database_urls_keep_their_driver_shape() -> None:
    """SQLAlchemy talks asyncpg; the checkpoint/store pool talks libpq."""
    assert settings.db.database_url.startswith("postgresql+asyncpg")
    assert settings.db.database_url_sync.startswith("postgresql://")
    assert "+asyncpg" not in settings.db.database_url_sync


def test_only_the_known_modules_read_the_sync_url() -> None:
    """A new reader of the libpq URL must be a deliberate decision, not a habit."""
    readers = set()
    for path in _python_files("agent_server", "graphs", "hub", "shop", "ml"):
        source = path.read_text()
        if "database_url_sync" in source:
            readers.add(_module_name(path))

    assert readers <= _SYNC_URL_ALLOWED, (
        "these modules read the psycopg URL; either they belong to the checkpoint/store/migration "
        f"path or they are crossing drivers: {sorted(readers - _SYNC_URL_ALLOWED)}"
    )


# --- two migration chains, two schemas ----------------------------------------


def _created_tables(versions_dir: Path) -> set[str]:
    """Table names created by the migrations under *versions_dir*."""
    names: set[str] = set()
    for path in versions_dir.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = ast.unparse(node.func)
            if func.endswith("create_table") and node.args and isinstance(node.args[0], ast.Constant):
                names.add(str(node.args[0].value))
    return names


def _metadata_tables(metadata: object) -> set[str]:
    return set(metadata.tables)  # ty: ignore[unresolved-attribute]


def test_the_two_migration_chains_own_disjoint_schemas() -> None:
    """Framework tables and hub tables must not be created by both chains."""
    from agent_server.repo.orm import Base as FrameworkBase
    from hub.db import Base as HubBase

    framework_tables = _metadata_tables(FrameworkBase.metadata)
    hub_tables = _metadata_tables(HubBase.metadata)

    assert framework_tables & hub_tables == set(), "a table is declared in both schemas"

    framework_created = _created_tables(_SRC / "agent_server" / "migrations" / "versions")
    hub_created = _created_tables(_SRC / "hub" / "migrations" / "versions")

    assert hub_created, "the hub chain should create the hub tables"
    assert hub_created <= hub_tables, f"hub migrations create unknown tables: {sorted(hub_created - hub_tables)}"
    assert framework_created & hub_tables == set(), "the framework chain must not create hub tables"
    assert hub_created & framework_tables == set(), "the hub chain must not create framework tables"


def test_the_two_chains_use_different_version_tables() -> None:
    """Sharing one version table would make each chain skip the other's revisions."""
    hub_env = (_SRC / "hub" / "migrations" / "env.py").read_text()
    framework_env = (_SRC / "agent_server" / "migrations" / "env.py").read_text()

    assert 'VERSION_TABLE = "alembic_version_hub"' in hub_env
    assert "alembic_version_hub" not in framework_env
    # The framework chain keeps alembic's default version_table, so it must not
    # have quietly adopted a custom one without this test being updated.
    assert "version_table" not in framework_env


# --- stdio stays a deployment privilege ---------------------------------------


def test_user_tier_connections_cannot_carry_a_command() -> None:
    """A user-facing API must never accept a shell command."""
    from typing import get_args

    from hub.models import McpConnectionCreate

    assert "command" not in McpConnectionCreate.model_fields
    transport = McpConnectionCreate.model_fields["transport"].annotation
    assert set(get_args(transport)) == {"streamable_http"}


@pytest.mark.parametrize("table", ["audit_log"])
def test_framework_metadata_matches_the_framework_chain(table: str) -> None:
    """A model added without a migration is a table that only exists in tests."""
    from agent_server.repo.orm import Base as FrameworkBase

    framework_created = _created_tables(_SRC / "agent_server" / "migrations" / "versions")

    assert table in _metadata_tables(FrameworkBase.metadata)
    assert table in framework_created, f"{table} has an ORM model but no framework migration"
