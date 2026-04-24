# =============================================================================
# FILE: backend/schemas/alert.py
# WHAT: Defines the shape of data crossing the API boundary for alerts.
#       AlertCreate  = what the CLIENT sends in (POST /api/v1/alerts body)
#       AlertResponse = what SENTINEL sends back (immediately after receiving alert)
# WHY:  SQLAlchemy models represent database rows. Pydantic schemas represent
#       API data. They are kept separate on purpose — you never expose raw DB
#       objects to the client (they might have internal fields you don't want shared).
# OOP:  Both classes inherit from Pydantic's BaseModel, which provides
#       automatic JSON parsing, type validation, and error messages for free.
#       field_validator is a classmethod — it belongs to the class, not an instance.
# CONNECTED TO:
#   → api/routers/alerts.py uses AlertCreate to parse the request body
#   → api/routers/alerts.py uses AlertResponse to shape the response
#   → db/repositories/incident_repo.py receives AlertCreate in repo.create()
# =============================================================================

import uuid                                              # for the UUID type in AlertResponse
from pydantic import BaseModel, Field, field_validator   # Pydantic tools for schema + validation


# AlertCreate: the shape of the JSON body the client sends in POST /api/v1/alerts.
# Pydantic automatically parses and validates the incoming JSON against this schema.
# If any field is missing or the wrong type, FastAPI returns a 422 error automatically.
class AlertCreate(BaseModel):
    service_name: str = Field(..., min_length=1)     # ... means required, no default
    severity: str                                     # validated below — must be P1/P2/P3
    title: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    error_type: str = Field(..., min_length=1)        # e.g. "db_timeout", "oom", "latency_spike"
    source: str = Field(..., min_length=1)            # e.g. "prometheus", "datadog", "pagerduty"

    # field_validator runs after Pydantic parses the field.
    # If severity isn't P1/P2/P3, it raises ValueError → FastAPI returns 422.
    # OOP concept: classmethod — belongs to the class itself, not an instance.
    @field_validator("severity")
    @classmethod
    def validate_severity(cls, v: str) -> str:
        if v not in ("P1", "P2", "P3"):
            raise ValueError("severity must be P1, P2, or P3")
        return v


# AlertResponse: what Sentinel sends back immediately after receiving an alert.
# Status 202 Accepted — "I got it, investigation is starting, check back later."
# The client uses incident_id to poll GET /api/v1/incidents/{id} for the result.
class AlertResponse(BaseModel):
    incident_id: uuid.UUID  # client uses this to fetch the investigation result later
    status: str             # "investigating" at this point
    message: str            # human-readable e.g. "Investigation started"
