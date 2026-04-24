# =============================================================================
# FILE: tests/unit/test_health.py
# WHAT: Unit tests for GET /health — the endpoint that checks DB and Redis.
# WHY:  The health endpoint is what load balancers and uptime monitors use
#       to decide if the service is alive. If it returns the wrong status code
#       or misreports a failure as success, the consequences are:
#         - 200 when DB is down → load balancer keeps sending traffic to a
#           broken pod, every user request fails silently
#         - 503 when everything is fine → load balancer removes the pod,
#           traffic drops for no reason
#       These tests verify the three outcomes that must always be correct:
#         1. DB ok + Redis ok  → 200, status "ok"
#         2. DB down           → 503, status "degraded"
#         3. Redis down        → 503, status "degraded"
# HOW WE TEST:
#   health_check() takes a DB session as a FastAPI Depends() argument.
#   We call it directly, passing a mock AsyncSession. This lets us simulate
#   DB success/failure without a real Postgres connection.
#   Redis is tested by patching aioredis.from_url at the module level.
# CONNECTED TO:
#   ← backend/api/routers/health.py  — the function under test
# =============================================================================

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from api.routers.health import health_check


def make_db(fail: bool = False):
    """
    Build a mock AsyncSession whose execute() either succeeds or raises.

    fail=False → simulates a working DB connection (SELECT 1 succeeds)
    fail=True  → simulates a broken DB connection (SELECT 1 raises Exception)
    """
    db = MagicMock()
    if fail:
        db.execute = AsyncMock(side_effect=Exception("connection refused"))
    else:
        db.execute = AsyncMock(return_value=MagicMock())
    return db


def make_redis_patch(fail: bool = False):
    """
    Build a mock Redis client and an AsyncMock for aioredis.from_url.

    aioredis.from_url() is called with `await` in health.py, so it must be
    an AsyncMock — a plain MagicMock is not awaitable.

    Returns (mock_from_url, mock_redis) so tests can assert on mock_redis.
    """
    mock_redis = MagicMock()
    if fail:
        mock_redis.ping = AsyncMock(side_effect=Exception("connection refused"))
    else:
        mock_redis.ping = AsyncMock()
    mock_redis.aclose = AsyncMock()

    # AsyncMock so `await aioredis.from_url(url)` resolves to mock_redis
    mock_from_url = AsyncMock(return_value=mock_redis)
    return mock_from_url, mock_redis


class TestHealthCheckStatus:

    async def test_returns_200_when_all_healthy(self):
        # DB executes fine, Redis pings fine → HTTP 200.
        # This is the happy path — the only time we return 200.
        db = make_db(fail=False)
        mock_from_url, _ = make_redis_patch(fail=False)

        with patch("api.routers.health.aioredis.from_url", mock_from_url):
            response = await health_check(db=db)

        assert response.status_code == 200

    async def test_returns_503_when_db_is_down(self):
        # DB raises → must return 503 regardless of Redis status.
        # Load balancer should stop sending traffic here.
        db = make_db(fail=True)
        mock_from_url, _ = make_redis_patch(fail=False)

        with patch("api.routers.health.aioredis.from_url", mock_from_url):
            response = await health_check(db=db)

        assert response.status_code == 503

    async def test_returns_503_when_redis_is_down(self):
        # Redis raises → must return 503 regardless of DB status.
        db = make_db(fail=False)
        mock_from_url, _ = make_redis_patch(fail=True)

        with patch("api.routers.health.aioredis.from_url", mock_from_url):
            response = await health_check(db=db)

        assert response.status_code == 503

    async def test_returns_503_when_both_are_down(self):
        # Both DB and Redis down → still 503, status "degraded".
        db = make_db(fail=True)
        mock_from_url, _ = make_redis_patch(fail=True)

        with patch("api.routers.health.aioredis.from_url", mock_from_url):
            response = await health_check(db=db)

        assert response.status_code == 503


class TestHealthCheckBody:

    async def test_overall_status_is_ok_when_healthy(self):
        # Response body must have status="ok" — not just a 200 status code.
        # Monitoring tools parse the JSON body, not just the HTTP status.
        db = make_db(fail=False)
        mock_from_url, _ = make_redis_patch(fail=False)

        with patch("api.routers.health.aioredis.from_url", mock_from_url):
            response = await health_check(db=db)

        body = json.loads(response.body)
        assert body["status"] == "ok"

    async def test_overall_status_is_degraded_when_db_fails(self):
        db = make_db(fail=True)
        mock_from_url, _ = make_redis_patch(fail=False)

        with patch("api.routers.health.aioredis.from_url", mock_from_url):
            response = await health_check(db=db)

        body = json.loads(response.body)
        assert body["status"] == "degraded"

    async def test_checks_dict_contains_database_and_redis_keys(self):
        # The checks dict must always have both keys.
        # Monitoring dashboards expect this exact structure.
        db = make_db(fail=False)
        mock_from_url, _ = make_redis_patch(fail=False)

        with patch("api.routers.health.aioredis.from_url", mock_from_url):
            response = await health_check(db=db)

        body = json.loads(response.body)
        assert "database" in body["checks"]
        assert "redis" in body["checks"]

    async def test_database_check_is_ok_when_healthy(self):
        db = make_db(fail=False)
        mock_from_url, _ = make_redis_patch(fail=False)

        with patch("api.routers.health.aioredis.from_url", mock_from_url):
            response = await health_check(db=db)

        body = json.loads(response.body)
        assert body["checks"]["database"] == "ok"

    async def test_database_check_shows_error_prefix_when_failing(self):
        # When DB is down, checks["database"] must start with "error:".
        # This lets monitoring tools parse the failure reason.
        db = make_db(fail=True)
        mock_from_url, _ = make_redis_patch(fail=False)

        with patch("api.routers.health.aioredis.from_url", mock_from_url):
            response = await health_check(db=db)

        body = json.loads(response.body)
        assert body["checks"]["database"].startswith("error:")

    async def test_redis_check_is_ok_when_healthy(self):
        db = make_db(fail=False)
        mock_from_url, _ = make_redis_patch(fail=False)

        with patch("api.routers.health.aioredis.from_url", mock_from_url):
            response = await health_check(db=db)

        body = json.loads(response.body)
        assert body["checks"]["redis"] == "ok"

    async def test_redis_check_shows_error_prefix_when_failing(self):
        db = make_db(fail=False)
        mock_from_url, _ = make_redis_patch(fail=True)

        with patch("api.routers.health.aioredis.from_url", mock_from_url):
            response = await health_check(db=db)

        body = json.loads(response.body)
        assert body["checks"]["redis"].startswith("error:")


class TestHealthCheckSafety:

    async def test_redis_connection_always_closed_on_success(self):
        # aclose() must be called after a successful ping.
        db = make_db(fail=False)
        mock_from_url, mock_redis = make_redis_patch(fail=False)

        with patch("api.routers.health.aioredis.from_url", mock_from_url):
            await health_check(db=db)

        mock_redis.aclose.assert_called_once()

    async def test_redis_connection_always_closed_on_ping_failure(self):
        # aclose() must still be called even when ping() raises.
        # Without try/finally in health.py, a failed ping leaks the connection.
        db = make_db(fail=False)
        mock_from_url, mock_redis = make_redis_patch(fail=True)

        with patch("api.routers.health.aioredis.from_url", mock_from_url):
            await health_check(db=db)

        mock_redis.aclose.assert_called_once()

    async def test_error_message_is_truncated(self):
        # Error messages are capped at 80 chars in the response body.
        # A very long DB error message must not be returned in full —
        # it could leak internal connection strings or credentials.
        db = make_db(fail=False)
        db.execute = AsyncMock(side_effect=Exception("x" * 200))
        mock_from_url, _ = make_redis_patch(fail=False)

        with patch("api.routers.health.aioredis.from_url", mock_from_url):
            response = await health_check(db=db)

        body = json.loads(response.body)
        # "error: " prefix (7 chars) + up to 80 chars of the message = 87 max
        assert len(body["checks"]["database"]) <= 87
