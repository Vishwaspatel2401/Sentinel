# =============================================================================
# FILE: backend/db/database.py
# WHAT: Sets up the async database connection to Postgres.
#       Provides three things the rest of the app uses:
#         1. engine       — the connection pool (created once at startup)
#         2. AsyncSessionLocal — factory that creates sessions on demand
#         3. get_db_session   — FastAPI dependency that hands a session to each request
#         4. Base         — parent class that all ORM models inherit from
# WHY:  Centralises all database plumbing in one place. Every other file just
#       does `from db.database import get_db_session` or `from db.database import Base`.
# CONNECTED TO:
#   ← config.py provides settings.database_url (the connection string)
#   → db/models.py imports Base and defines all tables on top of it
#   → db/repositories/incident_repo.py receives AsyncSession via get_db_session
#   → api/routers/alerts.py injects get_db_session via FastAPI Depends()
# =============================================================================

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker  # async SQLAlchemy tools
from sqlalchemy.orm import DeclarativeBase  # base class for all ORM models (new SQLAlchemy 2.0 style)
from typing import AsyncGenerator  # type hint for functions that yield values
from config import settings  # our pydantic settings — single source of truth for config


# --- Base class for all ORM models ---
# Every table (Incident, LogEntry, Deploy) will inherit from this.
# SQLAlchemy uses it to track all models and generate CREATE TABLE statements.
# OOP concept: Inheritance — all models extend Base, getting SQLAlchemy's tracking for free.
class Base(DeclarativeBase):
    pass


# --- Engine: the persistent connection pool to Postgres ---
# Created once at startup and shared across the entire app.
# pool_size=10 means 10 connections are kept open and ready.
# max_overflow=20 means under heavy load, 20 extra connections can be created temporarily.
# echo=True prints every SQL query to the terminal — useful for debugging.
engine = create_async_engine(
    settings.database_url,  # e.g. postgresql+asyncpg://sentinel:sentinel_dev@localhost/sentinel
    echo=settings.debug,    # logs every SQL query when debug=True
    pool_size=10,           # keep 10 connections ready at all times
    max_overflow=20,        # allow 20 extra connections under heavy load
)


# --- Session factory: stamps out fresh sessions on demand ---
# A session = one unit of work (open → query → commit → close).
# expire_on_commit=False means objects stay usable after a commit
# (otherwise SQLAlchemy would reset them, causing errors in async code).
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,    # use the async version of Session
    expire_on_commit=False, # keep objects alive after commit
)


# --- Dependency: hands a fresh session to each FastAPI request ---
# FastAPI calls this for every incoming request.
# yield pauses here — FastAPI runs the route handler with the session,
# then comes back to close it. The session is ALWAYS cleaned up, even if the request crashes.
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:  # opens a session
        yield session                           # hands it to the caller
                                                # session auto-closes when the block exits