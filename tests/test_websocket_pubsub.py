import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from denoiser.api.main import app
from denoiser.api.middleware import RateLimitMiddleware


class TestRedisRateLimiter:
    """Tests for the Redis-backed sliding window rate limiter."""

    @patch("denoiser.api.middleware.redis_asyncio.from_url")
    def test_rate_limiter_success_under_limit(self, mock_from_url):
        # Setup mock pipeline to return count = 5 (under the 10 max_requests limit)
        mock_redis = MagicMock()
        mock_pipeline = MagicMock()
        mock_pipeline.zadd = MagicMock()
        mock_pipeline.zremrangebyscore = MagicMock()
        mock_pipeline.zcard = MagicMock()
        mock_pipeline.expire = MagicMock()
        mock_pipeline.execute = AsyncMock(return_value=[True, True, 5, True])
        
        mock_redis.pipeline.return_value.__aenter__.return_value = mock_pipeline
        mock_from_url.return_value = mock_redis

        # Initialize rate limiter with low limit for testing
        middleware = RateLimitMiddleware(app, max_requests=10, window_seconds=60)
        middleware.redis = mock_redis

        with TestClient(middleware) as client:
            response = client.post("/ingest", json=[{"message": "test"}])
            assert response.status_code != 429

    @patch("denoiser.api.middleware.redis_asyncio.from_url")
    def test_rate_limiter_exceeded_limit(self, mock_from_url):
        # Setup mock pipeline to return count = 15 (exceeds the 10 max_requests limit)
        mock_redis = MagicMock()
        mock_pipeline = MagicMock()
        mock_pipeline.zadd = MagicMock()
        mock_pipeline.zremrangebyscore = MagicMock()
        mock_pipeline.zcard = MagicMock()
        mock_pipeline.expire = MagicMock()
        mock_pipeline.execute = AsyncMock(return_value=[True, True, 15, True])
        
        mock_redis.pipeline.return_value.__aenter__.return_value = mock_pipeline
        mock_from_url.return_value = mock_redis

        middleware = RateLimitMiddleware(app, max_requests=10, window_seconds=60)
        middleware.redis = mock_redis

        with TestClient(middleware) as client:
            response = client.post("/ingest", json=[{"message": "test"}])
            assert response.status_code == 429
            assert "Rate limit exceeded" in response.json()["error"]

    @patch("denoiser.api.middleware.redis_asyncio.from_url")
    def test_rate_limiter_fallback_on_redis_error(self, mock_from_url):
        # Setup mock pipeline to raise a connection error
        mock_redis = MagicMock()
        mock_pipeline = MagicMock()
        mock_pipeline.zadd = MagicMock()
        mock_pipeline.zremrangebyscore = MagicMock()
        mock_pipeline.zcard = MagicMock()
        mock_pipeline.expire = MagicMock()
        mock_pipeline.execute = AsyncMock(side_effect=Exception("Redis down"))
        
        mock_redis.pipeline.return_value.__aenter__.return_value = mock_pipeline
        mock_from_url.return_value = mock_redis

        # Fallback will use local dict. Max limit = 2.
        middleware = RateLimitMiddleware(app, max_requests=2, window_seconds=60)
        middleware.redis = mock_redis

        with TestClient(middleware) as client:
            # First request: OK (falls back to in-memory, count=1)
            resp1 = client.post("/ingest", json=[{"message": "test"}])
            assert resp1.status_code != 429

            # Second request: OK (count=2)
            resp2 = client.post("/ingest", json=[{"message": "test"}])
            assert resp2.status_code != 429

            # Third request: Blocked (count=3 > 2)
            resp3 = client.post("/ingest", json=[{"message": "test"}])
            assert resp3.status_code == 429


class TestWebSocketPubSub:
    """Tests verifying the horizontally scaled WebSocket pub/sub subscriber."""

    @pytest.mark.asyncio
    @patch("denoiser.api.routers_stream.get_current_user")
    async def test_websocket_pubsub_broadcasting(self, mock_get_current_user):
        # Redis is substituted through the runtime seam rather than by patching
        # a module global. `denoiser.api.main.redis_client` used to be the object
        # itself, so every consumer had to be patched by the path it imported it
        # from; there is now one place to replace it for all of them.
        from denoiser import runtime

        # Setup mock user
        mock_get_current_user.return_value = MagicMock(id=1, email="admin@semanticos.io")

        # Setup mock pubsub
        mock_pubsub = MagicMock()
        mock_pubsub.subscribe = AsyncMock()
        mock_pubsub.unsubscribe = AsyncMock()
        mock_pubsub.close = AsyncMock()
        
        # Async generator for message listener
        async def mock_listen():
            yield {
                "type": "message",
                "data": json.dumps({
                    "service": "payment-api",
                    "level": "ERROR",
                    "message": "Payment timeout error",
                    "timestamp": 1715934508.0
                })
            }
        
        mock_pubsub.listen = mock_listen
        
        # Use sync MagicMock for .pubsub() to ensure it returns the subscriber object directly
        mock_redis_client = MagicMock()
        mock_redis_client.pubsub = MagicMock(return_value=mock_pubsub)
        runtime.set_redis_client(mock_redis_client)

        try:
            client = TestClient(app)
            with client.websocket_connect("/stream?token=test_token") as websocket:
                data = websocket.receive_json()
                assert data["service"] == "payment-api"
                assert data["level"] == "ERROR"
                assert "Payment timeout" in data["message"]
        finally:
            runtime.reset()
