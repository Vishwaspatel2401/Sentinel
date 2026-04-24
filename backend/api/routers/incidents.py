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

import uuid                                             # for parsing the UUID path parameter
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession         # type hint for the injected DB session
from db.database import get_db_session                  # dependency that hands a fresh DB session per request
from db.repositories.incident_repo import IncidentRepository  # all DB logic for incidents
from api.dependencies.rate_limit import limiter          # shared rate limiter


# Group all incident-related read endpoints under /api/v1/incidents.
# tags=["incidents"] puts them in their own section in the /docs page.
router = APIRouter(prefix="/api/v1/incidents", tags=["incidents"])


# GET /api/v1/incidents/{incident_id}
# Returns the current state of an incident and its investigation result (if ready).
#
# Path parameter: incident_id — the UUID returned by POST /alerts.
# FastAPI automatically parses the string from the URL into a uuid.UUID object.
# If it's not a valid UUID, FastAPI returns a 422 before this function even runs.
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
