"""Executor factory — selects backend based on settings.

Mirrors the broker factory pattern in broker.py. The global `executor`
instance is used by API endpoints to submit jobs.
"""

import structlog

from agent_server.config.settings import settings
from agent_server.usecase.base_executor import BaseExecutor

logger = structlog.getLogger(__name__)


def _create_executor() -> BaseExecutor:
    """Select executor backend based on Redis configuration."""
    if settings.redis.REDIS_BROKER_ENABLED:
        from agent_server.usecase.execution.worker_executor import WorkerExecutor

        logger.info("Using Redis worker executor (BLPOP job queue)")
        return WorkerExecutor()

    from agent_server.usecase.execution.local_executor import LocalExecutor

    logger.info("Using local asyncio executor (in-process tasks)")
    return LocalExecutor()


executor: BaseExecutor = _create_executor()
