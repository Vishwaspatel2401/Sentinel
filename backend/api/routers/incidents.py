# =============================================================================
# FILE: backend/api/routers/incidents.py
# WHAT: Defines the GET /api/v1/incidents/{incident_id} HTTP endpoint.
#       This is how the client polls for investigation results after posting an alert.
# WHY:  The POST /alerts endpoint returns 202 immediately — investigation runs in
#       the background worker. This endpoint is how you check "is it done yet?".
# POLLING FLOW:
#   1. Client POSTs to /alerts → gets back incident_id
#   2. Client GETs /incidents/{incident_id} every few seconds
#   3. status = "investigating" → still running, keep polling
#   4. status = "resolved"     → done, read the resolution fields
#   5. status = "failed"       → worker crashed, resolution will be null
# CONNECTED TO:
#   ← main.py registers this router via app.include_router(incidents.router)
#   ← db/database.py provides get_db_session (injected DB session)
#   → db/repositories/incident_repo.py fetches the incident + resolution row
#   → db/models.py Incident has a .resolution relationship (one-to-one)
# =============================================================================

import json
import uuid                                             # for parsing the UUID path parameter
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession         # type hint for the injected DB session
import redis.asyncio as aioredis
from db.database import get_db_session                  # dependency that hands a fresh DB session per request
from db.repositories.incident_repo import IncidentRepository  # all DB logic for incidents
from api.dependencies.rate_limit import limiter          # shared rate limiter
from config import settings

QUEUE_KEY = "sentinel:alert:queue"
DEAD_KEY  = "sentinel:alert:dead"

# Group all incident-related read endpoints under /api/v1/incidents.
# tags=["incidents"] puts them in their own section in the /docs page.
router = APIRouter(prefix="/api/v1/incidents", tags=["incidents"])


# GET /api/v1/incidents/{incident_id}
# Returns the current state of an incident and its investigation result (if ready).
#
# Path parameter: incident_id — the UUID returned by POST /alerts.
# FastAPI automatically parses the string from the URL into a uuid.UUID object.
# If it's not a valid UUID, FastAPI returns a 422 before this function even runs.
@router.get("")
@limiter.limit("60/minute")
async def list_incidents(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """Return the 20 most recent incidents, newest first. Used by the dashboard."""
    repo = IncidentRepository(db)
    incidents = await repo.list_recent(limit=20)

    return [
        {
            "incident_id":  str(inc.id),
            "service_name": inc.service_name,
            "severity":     inc.severity,
            "title":        inc.title,
            "status":       inc.status,
            "created_at":   inc.created_at.isoformat() if inc.created_at else None,
            "confidence":   inc.resolution.confidence if inc.resolution else None,
        }
        for inc in incidents
    ]


@router.get("/{incident_id}")
@limiter.limit("60/minute")
async def get_incident(
    request: Request,                                # required by slowapi to read the rate limit key
    incident_id: uuid.UUID,                          # parsed from the URL path
    db: AsyncSession = Depends(get_db_session)       # fresh DB session injected for this request
):
    repo = IncidentRepository(db)

    # Fetch the incident from Postgres.
    # get_by_id uses selectinload — so incident.resolution is already loaded
    # (no second DB call needed, no lazy-load errors).
    incident = await repo.get_by_id(incident_id)

    # If no row found with this ID, return HTTP 404.
    # HTTPException tells FastAPI to stop here and return:
    #   {"detail": "Incident not found"} with status 404.
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")

    # Build the resolution block — None if the worker hasn't finished yet.
    # incident.resolution is the related Resolution ORM object (or None).
    # We pull out each field manually so we control exactly what's returned.
    resolution_data = None
    if incident.resolution:
        resolution_data = {
            "root_cause":     incident.resolution.root_cause,
            "confidence":     incident.resolution.confidence,   # float 0.0–1.0
            "suggested_fix":  incident.resolution.suggested_fix,
            "evidence":       incident.resolution.evidence,     # list of strings (JSONB)
            "llm_model_used": incident.resolution.llm_model_used,
        }

    # Return the full incident state as a plain dict.
    # FastAPI automatically serialises this to JSON.
    # Returning a dict (not a Pydantic model) is fine for read-only endpoints.
    return {
        "incident_id":   str(incident.id),          # UUID → string for JSON
        "service_name":  incident.service_name,
        "severity":      incident.severity,
        "title":         incident.title,
        "description":   incident.description,
        "error_type":    incident.error_type,
        "source":        incident.source,
        "status":        incident.status,           # "investigating" | "resolved" | "failed"
        "created_at":    incident.created_at.isoformat() if incident.created_at else None,
        "updated_at":    incident.updated_at.isoformat() if incident.updated_at else None,
        "resolution":    resolution_data,           # None while investigating, dict when done
    }


@router.post("/{incident_id}/requeue")
@limiter.limit("10/minute")
async def requeue_incident(
    request: Request,
    incident_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Move a failed incident from the dead letter queue back to the main alert queue.

    Use this when:
    - Anthropic had a temporary outage and you want to retry investigations
    - A transient DB error caused a job to fail and you want to retry it
    - You've fixed an underlying issue and want to re-investigate

    Flow:
    1. Verify the incident exists and is actually "failed"
    2. Reset its status to "investigating" in Postgres
    3. Remove it from the dead letter queue
    4. Push it back onto the main alert queue
    5. A worker picks it up and runs the pipeline again
    """
    repo = IncidentRepository(db)
    incident = await repo.get_by_id(incident_id)

    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")

    if incident.status != "failed":
        raise HTTPException(
            status_code=400,
            detail=f"Incident status is '{incident.status}' — only 'failed' incidents can be requeued"
        )

    # Reset status so the dashboard shows it as investigating again
    await repo.update_status(str(incident_id), "investigating")
    await db.commit()

    payload = json.dumps({"incident_id": str(incident_id)})

    r = await aioredis.from_url(settings.redis_url)
    try:
        # Remove from dead letter queue (LREM removes all matching entries)
        await r.lrem(DEAD_KEY, 0, payload)
        # Push to the front of the main queue (LPUSH = highest priority)
        await r.lpush(QUEUE_KEY, payload)
    finally:
        await r.aclose()

    return {
        "requeued":    True,
        "incident_id": str(incident_id),
        "message":     "Incident reset to 'investigating' and pushed back onto the alert queue",
    }
