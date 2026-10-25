"""Unit tests for RedisManager"""

from unittest.mock import AsyncMock, patch

import pytest
import redis.asyncio as aioredis
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff
from redis.exceptions import ConnectionError as RedisConnectionError

from agent_server.config.settings import settings
from agent_server.infra import redis as redis_manager_module
from agent_server.infra.redis import RedisManager


class TestRedisManager:
    """Test RedisManager lifecycle"""

    @pytest.mark.asyncio
    async def test_initialize_creates_pool_and_pings(self) -> None:
        """Test that initialize creates connection pool and verifies connectivity"""
        manager = RedisManager()

        mock_client = AsyncMock()
        mock_pool = AsyncMock()

        with (
            patch("agent_server.infra.redis.aioredis.ConnectionPool") as mock_pool_cls,
            patch("agent_server.infra.redis.aioredis.Redis", return_value=mock_client) as mock_redis_cls,
        ):
            mock_pool_cls.from_url.return_value = mock_pool

            await manager.initialize()

            # shared pool + the dedicated pool for blocking commands
            assert mock_pool_cls.from_url.call_count == 2
            assert mock_redis_cls.call_count == 2
            assert mock_redis_cls.call_args_list[0].kwargs == {"connection_pool": mock_pool}
            mock_client.ping.assert_awaited_once()

        # Clean up
        manager._client = None
        manager._pool = None
        manager._blocking_client = None
        manager._blocking_pool = None

    @pytest.mark.asyncio
    async def test_initialize_configures_retry_and_health_checks(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Pooled connections must survive server-side idle disconnects (#505).

        Non-default settings prove the values come from RedisSettings rather
        than literals. Patched through the settings object redis_manager
        holds, since a sibling test reloads agent_server.config.settings.
        """
        monkeypatch.setattr(redis_manager_module.settings.redis, "REDIS_HEALTH_CHECK_INTERVAL", 45)
        monkeypatch.setattr(redis_manager_module.settings.redis, "REDIS_RETRY_ATTEMPTS", 5)
        manager = RedisManager()
        mock_client = AsyncMock()

        with (
            patch("agent_server.infra.redis.aioredis.ConnectionPool") as mock_pool_cls,
            patch("agent_server.infra.redis.aioredis.Redis", return_value=mock_client),
        ):
            await manager.initialize()

            kwargs = mock_pool_cls.from_url.call_args_list[0].kwargs
            assert kwargs["health_check_interval"] == 45
            retry = kwargs["retry"]
            assert isinstance(retry, Retry)
            assert retry.get_retries() == 5
            assert isinstance(retry._backoff, ExponentialBackoff)
            assert any(issubclass(RedisConnectionError, e) for e in retry._supported_errors)
            assert RedisConnectionError in kwargs["retry_on_error"]

        manager._client = None
        manager._pool = None

    @pytest.mark.asyncio
    async def test_real_pool_connections_carry_retry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """End-to-end through the real ConnectionPool: connections built from
        the pool's kwargs must have retries (asyncio default is 0)."""
        monkeypatch.setattr(redis_manager_module.settings.redis, "REDIS_HEALTH_CHECK_INTERVAL", 45)
        monkeypatch.setattr(redis_manager_module.settings.redis, "REDIS_RETRY_ATTEMPTS", 5)
        manager = RedisManager()
        mock_client = AsyncMock()
        real_from_url = aioredis.ConnectionPool.from_url
        captured: list[aioredis.ConnectionPool] = []

        def capture_from_url(url: str, **kwargs: object) -> aioredis.ConnectionPool:
            captured.append(real_from_url("redis://localhost:1/0", **kwargs))
            return captured[-1]

        with (
            patch(
                "agent_server.infra.redis.aioredis.ConnectionPool.from_url",
                side_effect=capture_from_url,
            ),
            patch("agent_server.infra.redis.aioredis.Redis", return_value=mock_client),
        ):
            await manager.initialize()

        shared_pool, _blocking_pool = captured
        conn = shared_pool.make_connection()
        assert conn.retry.get_retries() == 5
        assert conn.health_check_interval == 45

        manager._client = None
        manager._pool = None

    @pytest.mark.asyncio
    async def test_initialize_is_idempotent(self) -> None:
        """Test that calling initialize twice doesn't create a second pool"""
        manager = RedisManager()
        manager._client = AsyncMock()  # Simulate already initialized

        with patch("agent_server.infra.redis.aioredis.ConnectionPool") as mock_pool_cls:
            await manager.initialize()

            mock_pool_cls.from_url.assert_not_called()

        # Clean up
        manager._client = None

    @pytest.mark.asyncio
    async def test_close_cleans_up(self) -> None:
        """Test that close disposes of client and pool"""
        manager = RedisManager()
        mock_client = AsyncMock()
        mock_pool = AsyncMock()
        manager._client = mock_client
        manager._pool = mock_pool

        await manager.close()

        mock_client.aclose.assert_awaited_once()
        mock_pool.disconnect.assert_awaited_once()
        assert manager._client is None
        assert manager._pool is None

    @pytest.mark.asyncio
    async def test_close_when_not_initialized(self) -> None:
        """Test that close is safe when not initialized"""
        manager = RedisManager()

        # Should not raise
        await manager.close()

    def test_get_client_returns_client(self) -> None:
        """Test get_client returns the initialized client"""
        manager = RedisManager()
        mock_client = AsyncMock()
        manager._client = mock_client

        result = manager.get_client()

        assert result is mock_client

        # Clean up
        manager._client = None

    def test_get_client_raises_when_not_initialized(self) -> None:
        """Test get_client raises RuntimeError when not initialized"""
        manager = RedisManager()

        with pytest.raises(RuntimeError, match="Redis not initialized"):
            manager.get_client()


class TestBlockingPool:
    """The dedicated pool for BLPOP: read timeout outlasts the block, no retries."""

    @pytest.mark.asyncio
    async def test_read_timeout_outlasts_the_blpop_block(self) -> None:
        """A BLPOP whose client read timeout fires at the end of the server-side
        block drops a job Redis already popped."""
        manager = RedisManager()
        mock_client = AsyncMock()

        with (
            patch("agent_server.infra.redis.aioredis.ConnectionPool") as mock_pool_cls,
            patch("agent_server.infra.redis.aioredis.Redis", return_value=mock_client),
        ):
            await manager.initialize()
            shared_kwargs = mock_pool_cls.from_url.call_args_list[0].kwargs
            blocking_pool = mock_pool_cls.from_url.return_value

            assert "socket_timeout" not in shared_kwargs
            update_kwargs = blocking_pool.connection_kwargs.update.call_args.kwargs
            assert update_kwargs["socket_timeout"] == settings.worker.BLOCKING_SOCKET_TIMEOUT_SECONDS
            assert settings.worker.BLOCKING_SOCKET_TIMEOUT_SECONDS >= settings.worker.BLPOP_TIMEOUT_SECONDS + 5, (
                "settings validation must keep the read timeout ahead of the block"
            )

        manager._client = None
        manager._pool = None
        manager._blocking_client = None
        manager._blocking_pool = None

    @pytest.mark.asyncio
    async def test_ignores_timeout_and_retry_params_in_redis_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """redis-py lets URL query params override from_url kwargs; socket_timeout=5 or
        retry_on_timeout=true in REDIS_URL must not reintroduce the BLPOP race."""
        monkeypatch.setattr(
            redis_manager_module.settings.redis,
            "REDIS_URL",
            "redis://localhost:1/0?socket_timeout=5&retry_on_timeout=true",
        )
        manager = RedisManager()
        mock_client = AsyncMock()
        real_from_url = aioredis.ConnectionPool.from_url
        pools: list[aioredis.ConnectionPool] = []

        def capture_from_url(url: str, **kwargs: object) -> aioredis.ConnectionPool:
            pools.append(real_from_url(url, **kwargs))
            return pools[-1]

        with (
            patch("agent_server.infra.redis.aioredis.ConnectionPool.from_url", side_effect=capture_from_url),
            patch("agent_server.infra.redis.aioredis.Redis", return_value=mock_client),
        ):
            await manager.initialize()

        shared_pool, blocking_pool = pools
        assert shared_pool.make_connection().socket_timeout == 5
        blocking_conn = blocking_pool.make_connection()
        assert blocking_conn.socket_timeout == settings.worker.BLOCKING_SOCKET_TIMEOUT_SECONDS
        # a silent BLPOP retry would discard the reply of a job the first attempt popped
        assert blocking_conn.retry is None or blocking_conn.retry.get_retries() == 0
        assert blocking_conn.retry_on_error == []

        manager._client = None
        manager._pool = None
        manager._blocking_client = None
        manager._blocking_pool = None

    async def test_close_cleans_up_the_blocking_pool(self) -> None:
        manager = RedisManager()
        manager._client = AsyncMock()
        manager._pool = AsyncMock()
        blocking_client = AsyncMock()
        blocking_pool = AsyncMock()
        manager._blocking_client = blocking_client
        manager._blocking_pool = blocking_pool

        await manager.close()

        blocking_client.aclose.assert_awaited_once()
        blocking_pool.disconnect.assert_awaited_once()
        assert manager._blocking_client is None
        assert manager._blocking_pool is None
