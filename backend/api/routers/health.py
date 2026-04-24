# =============================================================================
# FILE: backend/api/routers/health.py
# WHAT: GET /health — checks DB and Redis connectivity and reports status.
# WHY:  Every production service needs a health endpoint because:
#         1. Load balancers use it to decide whether to send traffic here
#         2. Kubernetes liveness/readiness probes call it to restart sick pods
#         3. Uptime monitors (PagerDuty, Better Uptime) call it every 60s
#         4. You can curl it yourself to confirm the service is actually up
#       Without /health, you're flying blind — you only find out the service
#       is down when users complain.
# RESPONSE:
#   200 — all checks passed:
#     {"status": "ok", "checks": {"database": "ok", "redis": "ok"}}
#   503 — one or more checks failed:
#     {"status": "degraded", "checks": {"database": "ok", "redis": "error: ..."}}
# NOTE: /health is intentionally NOT protected by API key auth.
#       Load balancers and uptime monitors call it without credentials.
#       It reveals no sensitive data — just connectivity status.
# CONNECTED TO:
#   ← main.py               — registered via app.include_router(health.router)
#   ← db/database.py        — get_db_session provides the DB session
#   ← config.py             — redis_url for the Redis ping
# =============================================================================

import logging
import redis.asyncio as aioredis
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text                    # text() wraps raw SQL safely
from db.database import get_db_session
from config import settings

logger = logging.getLogger(__name__)

# No prefix — /health lives at the root, not under /api/v1/
# Keeping it at the root is the universal convention — every tool expects /health
router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check(db: AsyncSession = Depends(get_db_session)):
    """
    Check connectivity to Postgres and Redis.
    Returns 200 if both are reachable, 503 if either is down.
    """
    checks = {}

    # ── Check Postgres ─────────────────────────────────────────────────────────
    # SELECT 1 is the lightest possible query — no table scan, no data returned.
    # If it succeeds, we have a working DB connection. If it raises, DB is down.
    try:
        await db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:
        # Truncate the error — we don't want internal connection details leaked
        # in the response body (security), but we do want them in the logs.
        logger.error("Health check — database unreachable", extra={"error": str(e)})
        checks["database"] = f"error: {str(e)[:80]}"

    # ── Check Redis ────────────────────────────────────────────────────────────
    # PING is the Redis equivalent of SELECT 1 — lightest possible check.
    # We open and close the connection here (not reusing a pool) because
    # the worker manages its own Redis connection separately.
    try:
        r = await aioredis.from_url(settings.redis_url)
        await r.ping()
        await r.aclose()
        checks["redis"] = "ok"
    except Exception as e:
        logger.error("Health check — Redis unreachable", extra={"error": str(e)})
        checks["redis"] = f"error: {str(e)[:80]}"

    # ── Determine overall status ───────────────────────────────────────────────
    # "ok" only if ALL checks passed. Any single failure = "degraded".
    all_ok = all(v == "ok" for v in checks.values())
    overall = "ok" if all_ok else "degraded"
    http_status = 200 if all_ok else 503

    # Log the result — useful for spotting flapping health checks in production
    if all_ok:
        logger.info("Health check passed", extra={"checks": checks})
    else:
        logger.warning("Health check degraded", extra={"checks": checks})

    # JSONResponse lets us set the status code dynamically (200 or 503).
    # A plain `return {}` always returns 200 — can't do that here.
    return JSONResponse(
        status_code=http_status,
        content={"status": overall, "checks": checks},
    )
