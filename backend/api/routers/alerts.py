# =============================================================================
# FILE: backend/api/routers/alerts.py
# WHAT: Defines the POST /api/v1/alerts HTTP endpoint.
#       This is the front door of Sentinel — where alerts arrive from monitoring tools.
# WHY:  Separating routes into their own files keeps main.py clean.
#       As the app grows, you add more routers (incidents.py, health.py) without
#       touching this file.
# FLOW: Client sends POST → FastAPI validates body → IncidentRepository saves to DB
#       → responds 202 immediately → worker picks it up from Redis (Day 7)
# CONNECTED TO:
#   ← main.py registers this router via app.include_router(alerts.router)
#   ← schemas/alert.py provides AlertCreate (request) and AlertResponse (response)
#   ← db/database.py provides get_db_session (injected DB session)
#   → db/repositories/incident_repo.py does the actual DB insert
# =============================================================================

import json                                        # for serialising the Redis payload
import redis.asyncio as aioredis                  # async Redis client
from fastapi import APIRouter, Depends, Request  # Request needed for rate limiter key func
from sqlalchemy.ext.asyncio import AsyncSession  # type hint for the DB session
from db.database import get_db_session           # dependency that hands a fresh DB session per request
from db.repositories.incident_repo import IncidentRepository  # all DB logic for incidents
from schemas.alert import AlertCreate, AlertResponse           # request + response shapes
from config import settings                      # redis_url from .env
from api.dependencies.rate_limit import limiter  # shared rate limiter

# The Redis list key the worker listens on — must match QUEUE_KEY in investigation_worker.py
QUEUE_KEY = "sentinel:alert:queue"

# APIRouter groups all alert-related endpoints together.
# prefix means every route here starts with /api/v1/alerts.
# tags=["alerts"] groups them under "alerts" in the auto-generated /docs page.
router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])


# POST /api/v1/alerts — receives an alert, creates an incident row, returns immediately.
#
# FastAPI automatically:
#   1. Parses the JSON body into AlertCreate (validated by Pydantic)
#   2. Injects a fresh DB session via Depends(get_db_session)
#   3. Serializes the return value into AlertResponse JSON
#
# status_code=202 = "Accepted" — not 200 "OK Done".
# 202 means: "I received it and processing has started, but it's not finished yet."
# Honest — the investigation runs in the background worker, not inside this request.
@router.post("", response_model=AlertResponse, status_code=202)
@limiter.limit("20/minute")
async def create_alert(
    request: Request,                            # required by slowapi to read the rate limit key
    alert: AlertCreate,                          # parsed + validated from the JSON request body
    db: AsyncSession = Depends(get_db_session)   # fresh DB session injected for this request
):
    repo = IncidentRepository(db)                # pass this request's session into the repo
    incident = await repo.create(alert)          # insert the incident row into Postgres

    # Push the incident_id to the Redis queue so the worker picks it up.
    # LPUSH adds to the LEFT of the list — worker uses BLPOP from the left too.
    # We open and close the Redis connection per request (simple, safe for now).
    r = await aioredis.from_url(settings.redis_url)
    await r.lpush(QUEUE_KEY, json.dumps({"incident_id": str(incident.id)}))
    await r.aclose()   # always close — prevents connection leaks

    # Return 202 immediately — investigation runs in the background worker.
    # The client uses incident_id to poll GET /api/v1/incidents/{id} for results.
    return AlertResponse(
        incident_id=incident.id,
        status=incident.status,                  # "investigating" at this point
        message="Investigation started"
    )