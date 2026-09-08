"""Per-identity rate limiting as a FastAPI dependency.

Built on the `limits` library (the engine behind flask-limiter/slowapi),
wired explicitly instead of via decorator magic:

- **Key by identity**: authenticated users are limited per `user.identity`;
  anonymous traffic falls back to client IP. One abusive tenant never
  starves the others.
- **Storage follows the deployment**: Redis (shared across replicas) when the
  broker is enabled, in-memory otherwise.
- **Two tiers**: a generous default for read endpoints, a tighter one for
  run/streaming endpoints (they hold LLM connections open).
- Exceeded limits raise 429 in the Agent Protocol error shape with a
  Retry-After header.

Disabled by default; enable with `RATE_LIMIT_ENABLED=true`.
"""

import structlog
from fastapi import Depends, HTTPException, Request
from limits import parse
from limits.strategies import MovingWindowRateLimiter

from agent_server.auth.deps import get_current_user
from agent_server.config.settings import settings
from agent_server.domain.user import User

logger = structlog.get_logger(__name__)

_limiter: MovingWindowRateLimiter | None = None


def _get_limiter() -> MovingWindowRateLimiter:
    """Lazy singleton: Redis when the broker is enabled, memory otherwise."""
    global _limiter
    if _limiter is None:
        if settings.redis.REDIS_BROKER_ENABLED:
            try:
                from limits.storage import storage_from_string

                storage = storage_from_string(settings.redis.REDIS_URL, key_prefix="RATE_LIMIT")
                _limiter = MovingWindowRateLimiter(storage)
                logger.info("rate_limit_storage", backend="redis")
                return _limiter
            except Exception as e:
                logger.warning("rate_limit_redis_unavailable_falling_back_to_memory", error=str(e))
        _limiter = MovingWindowRateLimiter(_memory_storage())
        logger.info("rate_limit_storage", backend="memory")
    return _limiter


def _memory_storage():
    from limits.storage import MemoryStorage

    return MemoryStorage()


async def _enforce(request: Request, user: User, tier: str, limit_expr: str) -> None:
    if not settings.app.RATE_LIMIT_ENABLED:
        return
    key = (
        f"user:{user.identity}"
        if user.identity != "anonymous"
        else f"ip:{request.client.host if request.client else 'unknown'}"
    )
    limit = parse(limit_expr)
    if not _get_limiter().hit(limit, tier, key):
        logger.warning("rate_limit_exceeded", tier=tier, key=key, path=request.url.path)
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded. Retry later.",
            headers={"Retry-After": str(int(limit.get_expiry()))},
        )


async def rate_limit_default(request: Request, user: User = Depends(get_current_user)) -> None:
    """Generous tier for ordinary endpoints (`RATE_LIMIT_DEFAULT`)."""
    await _enforce(request, user, "default", settings.app.RATE_LIMIT_DEFAULT)


async def rate_limit_runs(request: Request, user: User = Depends(get_current_user)) -> None:
    """Tighter tier for run/streaming endpoints (`RATE_LIMIT_RUNS`)."""
    await _enforce(request, user, "runs", settings.app.RATE_LIMIT_RUNS)
