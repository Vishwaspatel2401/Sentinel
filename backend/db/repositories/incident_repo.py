# =============================================================================
# FILE: backend/db/repositories/incident_repo.py
# WHAT: All database operations for the incidents table, in one class.
#       No other file should write SQLAlchemy queries for incidents.
# WHY:  Repository pattern — centralises DB logic so if the schema changes,
#       you fix it in ONE place, not across 10 files.
# OOP:  Encapsulation — the DB complexity (sessions, commits, queries) is
#       hidden inside this class. Callers just call repo.create(alert).
#       Dependency Injection — the session is passed IN from outside,
#       not created inside. This makes the class easy to test with a fake session.
# CONNECTED TO:
#   ← db/database.py provides AsyncSession (injected via get_db_session)
#   ← db/models.py provides the Incident ORM class
#   ← schemas/alert.py provides AlertCreate (the data shape coming in)
#   → api/routers/alerts.py creates IncidentRepository and calls repo.create()
#   → api/routers/incidents.py calls repo.get_by_id() to fetch incident + resolution
#   → workers/investigation_worker.py calls repo.update_status() after investigation
# =============================================================================

import uuid                                              # for type hints on UUID parameters
from sqlalchemy.ext.asyncio import AsyncSession          # async DB session type
from sqlalchemy import select, update                    # SQLAlchemy query builders
from sqlalchemy.orm import selectinload                  # tells SQLAlchemy to eagerly load related rows
                                                         # in a second SELECT — avoids "lazy load after
                                                         # session closed" errors on incident.resolution
from db.models import Incident                           # the ORM model for the incidents table
from schemas.alert import AlertCreate                    # the Pydantic schema for incoming alerts


# Repository pattern: ALL database operations for the incidents table live here.
# Nothing outside this class should write raw SQLAlchemy queries for incidents.
# OOP concept: Encapsulation — DB logic is hidden inside one class.
class IncidentRepository:

    # Constructor: stores the session so every method can use it.
    # The session is injected from outside (Dependency Injection) — this class
    # doesn't create its own session, it receives one. Makes testing easier.
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, alert: AlertCreate) -> Incident:
        # Build a new Incident ORM object from the incoming alert data.
        # Status defaults to "investigating" — defined in the model.
        incident = Incident(
            service_name=alert.service_name,
            severity=alert.severity,
            title=alert.title,
            description=alert.description,
            error_type=alert.error_type,
            source=alert.source,
        )

        self.db.add(incident)          # stage the new row — not written to DB yet
        await self.db.commit()         # write to DB — this is when the INSERT happens
        await self.db.refresh(incident) # reload from DB so server_defaults (created_at, id) are populated
        return incident

    async def get_by_id(self, id: uuid.UUID) -> Incident | None:
        # select() builds a SELECT query — equivalent to: SELECT * FROM incidents WHERE id = ?
        #
        # selectinload(Incident.resolution) tells SQLAlchemy to also fetch the related
        # Resolution row in a second query (SELECT * FROM resolutions WHERE incident_id = ?).
        # This happens automatically, in the same async call, before the session closes.
        #
        # WITHOUT selectinload: accessing incident.resolution after this method returns
        # would raise MissingGreenlet / lazy load error — SQLAlchemy async sessions
        # don't allow lazy (on-demand) loading because there's no event loop at that point.
        # WITH selectinload: the Resolution is already loaded into memory, safe to access anywhere.
        result = await self.db.execute(
            select(Incident)
            .options(selectinload(Incident.resolution))  # eagerly load the joined resolution row
            .where(Incident.id == id)
        )
        return result.scalar_one_or_none()  # returns the object or None if not found — never raises

    async def list_recent(self, limit: int = 20) -> list[Incident]:
        # Fetch the most recent incidents, newest first, with resolutions pre-loaded.
        # Used by the dashboard to show all active and resolved investigations.
        result = await self.db.execute(
            select(Incident)
            .options(selectinload(Incident.resolution))
            .order_by(Incident.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def update_status(self, id: uuid.UUID, status: str) -> None:
        # update() builds an UPDATE query — equivalent to: UPDATE incidents SET status=? WHERE id=?
        await self.db.execute(
            update(Incident).where(Incident.id == id).values(status=status)
        )
        await self.db.commit()          # commit the UPDATE to the DB
