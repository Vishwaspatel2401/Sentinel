# =============================================================================
# FILE: backend/db/models.py
# WHAT: Defines all database tables as Python classes (ORM models).
#       Each class = one table. Each class attribute = one column.
#       SQLAlchemy reads these and generates the SQL CREATE TABLE statements.
# WHY:  You write Python, SQLAlchemy handles the SQL. You never write raw SQL
#       for schema definition — only for queries (and even those are abstracted).
# OOP:  Every model inherits from Base (defined in database.py).
#       Relationships let you navigate between tables using Python attributes
#       instead of writing JOIN queries.
# CONNECTED TO:
#   ← db/database.py provides Base (parent class for all models)
#   → alembic/versions/*.py — Alembic reads these models to generate migrations
#   → db/repositories/incident_repo.py imports Incident to query the table
#   → services/log_service.py imports LogEntry to query the table
#   → services/deploy_service.py imports Deploy to query the table
#
# TABLE RELATIONSHIPS:
#   Incident (1) ──→ (many) LogEntry   [one incident has many log entries]
#   Incident (1) ──→ (1)    Resolution [one incident has one resolution]
#   Deploy         stands alone        [not linked to incidents — queried by service_name]
# =============================================================================

import uuid                                          # for generating unique IDs
import sqlalchemy as sa                              # core SQLAlchemy — column types, constraints
from sqlalchemy.orm import Mapped, mapped_column, relationship  # SQLAlchemy 2.0 ORM tools
from sqlalchemy.dialects.postgresql import UUID, JSONB          # Postgres-specific column types
from datetime import datetime                        # for timestamp fields
from db.database import Base                         # our Base class — all models inherit from this


# --- Incident ---
# Represents one alert that came in. The top-level record everything else links to.
class Incident(Base):
    __tablename__ = "incidents"                      # actual table name in Postgres

    # UUID primary key — globally unique, harder to enumerate than integers
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),                          # store as real UUID type in Postgres
        primary_key=True,
        default=uuid.uuid4                           # auto-generate a new UUID if none provided
    )

    service_name: Mapped[str] = mapped_column(sa.String(100), nullable=False)   # which service fired the alert
    severity: Mapped[str] = mapped_column(sa.String(10), nullable=False)         # P1 / P2 / P3
    title: Mapped[str] = mapped_column(sa.String(255), nullable=False)           # short alert title
    description: Mapped[str] = mapped_column(sa.Text, nullable=False)            # full alert description
    error_type: Mapped[str] = mapped_column(sa.String(100), nullable=False)      # e.g. "db_timeout"
    source: Mapped[str] = mapped_column(sa.String(100), nullable=False)          # e.g. "prometheus"

    # status tracks where the investigation is — starts as "investigating", ends as "resolved"
    status: Mapped[str] = mapped_column(sa.String(50), nullable=False, default="investigating")

    # server_default = Postgres sets this automatically — more reliable than Python-side defaults
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now()                 # Postgres NOW() function
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        onupdate=sa.func.now()                       # auto-updates whenever this row is modified
    )

    # OOP: Relationships — lets you do incident.log_entries instead of writing a JOIN query
    # "LogEntry" in quotes = forward reference (LogEntry is defined below, not yet when Python reads this)
    log_entries: Mapped[list["LogEntry"]] = relationship(back_populates="incident")
    resolution: Mapped["Resolution"] = relationship(back_populates="incident", uselist=False)  # uselist=False = one-to-one


# --- LogEntry ---
# Mock log data. In production this would come from Datadog/CloudWatch.
# For the portfolio, we seed these rows to simulate real logs.
class LogEntry(Base):
    __tablename__ = "log_entries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Foreign key — links this log entry back to the incident it belongs to
    incident_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("incidents.id"),               # references the incidents table
        nullable=False
    )

    service_name: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    level: Mapped[str] = mapped_column(sa.String(10), nullable=False)           # ERROR / WARN / INFO
    message: Mapped[str] = mapped_column(sa.Text, nullable=False)               # the actual log line
    timestamp: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)

    # back_populates = the other side of the relationship — must match exactly
    incident: Mapped["Incident"] = relationship(back_populates="log_entries")


# --- Deploy ---
# Mock deployment history. Investigator checks if a recent deploy caused the incident.
class Deploy(Base):
    __tablename__ = "deploys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    service_name: Mapped[str] = mapped_column(sa.String(100), nullable=False, index=True)  # index=True speeds up lookups by service
    version: Mapped[str] = mapped_column(sa.String(50), nullable=False)          # e.g. "v2.4.1"
    deployed_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    deployed_by: Mapped[str] = mapped_column(sa.String(100), nullable=False)     # e.g. "ci-bot"
    diff_summary: Mapped[str] = mapped_column(sa.Text, nullable=False)           # what changed — e.g. "pool_size: 20→5"


# --- Resolution ---
# The AI's output for one incident. One-to-one with Incident.
class Resolution(Base):
    __tablename__ = "resolutions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # unique=True enforces one resolution per incident at the DB level
    incident_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        sa.ForeignKey("incidents.id"),
        nullable=False,
        unique=True                                  # one resolution per incident, no duplicates
    )

    root_cause: Mapped[str] = mapped_column(sa.Text, nullable=False)            # what the AI thinks caused it
    confidence: Mapped[float] = mapped_column(sa.Float, nullable=False)         # 0.0 to 1.0
    suggested_fix: Mapped[str] = mapped_column(sa.Text, nullable=False)         # what the engineer should do
    llm_model_used: Mapped[str] = mapped_column(sa.String(100), nullable=False) # which model produced this

    # JSONB = Postgres stores this as real JSON — queryable, indexable, flexible
    # stores a list of strings like ["847 connection refused errors", "pool_size changed 20→5"]
    evidence: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    resolved_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now()
    )

    # back_populates must match the field name on the Incident side
    incident: Mapped["Incident"] = relationship(back_populates="resolution")
