# =============================================================================
# FILE: backend/workers/investigation_worker.py
# WHAT: Background process that pulls alerts from the Redis queue and runs
#       the full multi-agent investigation pipeline for each one.
# WHY:  The FastAPI endpoint responds in <10ms (just saves to DB + enqueues).
#       The actual investigation takes 10-30 seconds (4 LLM calls, tool calls).
#       Keeping them separate means the API is always fast.
# PATTERN: Producer/Consumer — FastAPI (producer) pushes to Redis queue,
#          this worker (consumer) pulls and processes with BLPOP.
# WEEK 2 CHANGE:
#   Week 1: called InvestigationService — one LLM call, sequential pipeline
#   Week 2: calls AgentOrchestrator    — 4 agents, ReAct loop, tool calling
#   The worker itself barely changed — only the service it calls changed.
#   This is the power of the Facade pattern: AgentOrchestrator hides complexity.
# FLOW:
#   FastAPI POST /alerts → saves incident → pushes incident_id to Redis
#   This worker         → pulls incident_id → runs 4 agents → saves result
# CONNECTED TO:
#   ← api/routers/alerts.py         — pushes to "sentinel:alert:queue"
#   → services/agent_orchestrator.py — runs the full agent pipeline
#   → db/database.py                — AsyncSessionLocal for per-job DB sessions
#   → services/llm_service.py       — created once, shared (circuit breaker state)
#   → services/rag_service.py       — created once, shared (model loaded once)
# RUN: python -m workers.investigation_worker  (from backend/ folder)
# =============================================================================

import asyncio                                            # async event loop
import json                                               # deserialise Redis payload
import logging                                            # structured logging
import sys                                               # path manipulation
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# ↑ adds backend/ to Python path so "from services.x import X" works

import redis.asyncio as aioredis                          # async Redis client
from core.logging_config import setup_logging             # JSON log format
from db.database import AsyncSessionLocal                 # session factory
from db.repositories.incident_repo import IncidentRepository  # fetch incident by ID
from services.agent_orchestrator import AgentOrchestrator # the full 4-agent pipeline
from services.llm_service import LLMService               # shared — circuit breaker persists
from services.rag_service import RAGService               # shared — model loaded once
from config import settings                               # redis_url from .env

logger = logging.getLogger(__name__)

QUEUE_KEY = "sentinel:alert:queue"   # must match the key in alerts.py


async def process_one(
    incident_id: str,
    llm_svc: LLMService,
    rag_svc: RAGService,
) -> None:
    # Each investigation gets its own DB session — opened here, closed when done.
    # async with = the session is automatically closed even if an exception is raised.
    async with AsyncSessionLocal() as db:
        repo = IncidentRepository(db)

        # Fetch the full Incident ORM object from Postgres.
        # Uses selectinload internally so incident.resolution is pre-loaded.
        incident = await repo.get_by_id(incident_id)

        if not incident:
            logger.warning("Incident not found in DB — skipping", extra={"incident_id": incident_id})
            return

        try:
            # Run the full 4-agent pipeline:
            #   ClassifierAgent → InvestigatorAgent → HypothesisAgent → ResponderAgent
            # AgentOrchestrator handles all agent wiring, tool creation, and DB saves.
            orchestrator = AgentOrchestrator(db, llm_svc, rag_svc)
            await orchestrator.run(incident)
            logger.info("Investigation complete", extra={"incident_id": incident_id})

        except Exception as e:
            # If anything crashes in the pipeline, mark the incident as failed.
            # This prevents it from showing as "investigating" forever on the dashboard.
            logger.error(
                "Investigation pipeline failed",
                extra={"incident_id": incident_id, "error": str(e)},
                exc_info=True,    # includes full traceback in the structured log
            )
            await repo.update_status(incident_id, "failed")
            await db.commit()


async def main() -> None:
    # Configure JSON logging for the worker process.
    # The FastAPI process calls setup_logging() in main.py.
    # The worker is a separate process — it must configure logging independently.
    setup_logging(settings.log_level)

    logger.info("Starting Sentinel investigation worker", extra={"mode": "agent", "week": 2})

    # Create shared instances — once per worker lifetime, not per job.
    # LLMService: shared so the circuit breaker failure count persists across all jobs.
    # RAGService: shared because loading the embedding model takes ~2s — too slow per job.
    llm_svc = LLMService()
    rag_svc = RAGService()

    redis = await aioredis.from_url(settings.redis_url)
    logger.info("Connected to Redis — listening for alerts", extra={"queue": QUEUE_KEY})

    while True:
        # BLPOP blocks until an item appears in the queue, then pops and returns it.
        # timeout=5 means: if nothing arrives in 5s, return None and loop again.
        # This lets the worker wake up periodically (useful for graceful shutdown).
        result = await redis.blpop(QUEUE_KEY, timeout=5)

        if result is None:
            continue    # timeout — queue was empty, wait again

        _, raw = result                         # BLPOP returns (key, value) tuple
        payload = json.loads(raw)              # parse {"incident_id": "uuid-string"}
        incident_id = payload["incident_id"]

        logger.info("Received alert — starting agent pipeline", extra={"incident_id": incident_id})
        await process_one(incident_id, llm_svc, rag_svc)


if __name__ == "__main__":
    asyncio.run(main())
