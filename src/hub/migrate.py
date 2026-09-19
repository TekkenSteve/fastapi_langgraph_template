"""Hub migration runner: apply the hub's own alembic chain.

Mirrors the framework's lock-free precheck (repo/migrations.py) but against
``alembic_version_hub``, so business chains can apply at app startup without
touching the framework chain. Called from the http app's lifespan.
"""

from pathlib import Path

import psycopg
import structlog
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from agent_server.config.settings import settings

logger = structlog.getLogger(__name__)

_HUB_DIR = Path(__file__).resolve().parent
VERSION_TABLE = "alembic_version_hub"


def _get_alembic_config() -> Config:
    """Alembic config for the hub chain (script_location relative to hub/)."""
    cfg = Config(str(_HUB_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(_HUB_DIR / "migrations"))
    return cfg


def _is_database_up_to_date(cfg: Config) -> bool:
    """Compare alembic_version_hub with the chain head via psycopg directly
    (SQLAlchemy's URL parser breaks on libpq comma-host syntax — same reason
    the framework precheck bypasses it)."""
    head = ScriptDirectory.from_config(cfg).get_current_head()
    with psycopg.connect(settings.db.database_url_sync) as conn, conn.cursor() as cur:
        try:
            cur.execute(f"SELECT version_num FROM {VERSION_TABLE} LIMIT 1")
            row = cur.fetchone()
            current = row[0] if row else None
        except psycopg.errors.UndefinedTable:
            conn.rollback()
            current = None
    return current == head


def run_hub_migrations_if_needed() -> None:
    """Skip when alembic_version_hub is already at head; upgrade otherwise."""
    cfg = _get_alembic_config()
    try:
        if _is_database_up_to_date(cfg):
            logger.debug("hub schema already at migration head; skipping upgrade")
            return
    except Exception as exc:
        logger.debug("hub revision precheck failed; falling back to full upgrade", error=str(exc))

    logger.info("running hub database migrations")
    command.upgrade(cfg, "head")
    logger.info("hub database migrations completed")
