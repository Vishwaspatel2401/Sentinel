# =============================================================================
# FILE: backend/workers/investigation_worker.py
# WHAT: Background process that pulls alerts from the Redis queue and runs
#       the full multi-agent investigation pipeline for each one.
# WHY:  The FastAPI endpoint responds in <10ms (just saves to DB + enqueues).
#       The actual investigation takes 10-30 seconds (4 LLM calls, tool calls).
#       Keeping them separate means the API is always fast.
# GRACEFUL SHUTDOWN:
#   Docker sends SIGTERM when you run `docker-compose down`.
#   Without handling it, the worker is force-killed mid-investigation —
#   the incident stays as "investigating" forever, the user polls forever.
#   With SIGTERM handling:
#     1. Signal arrives → shutdown_event is set
#     2. Current investigation finishes (can take up to 30s)
#     3. Worker exits cleanly, Redis connection closed
#   docker-compose.yml sets stop_grace_period: 60s to allow this.
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
import signal                                             # SIGTERM / SIGINT handling
import time
import uuid                                               # for converting string IDs from Redis payload
import redis.asyncio as aioredis                          # async Redis client
from core.logging_config import setup_logging             # JSON log format
from core.metrics import (                                # Prometheus metrics
    INVESTIGATIONS_TOTAL,
    INVESTIGATION_DURATION,
    ACTIVE_INVESTIGATIONS,
    QUEUE_DEPTH,
)
from core.constants import QUEUE_KEY, DEAD_KEY            # shared Redis queue key names
from db.database import AsyncSessionLocal                 # session factory
from db.repositories.incident_repo import IncidentRepository  # fetch incident by ID
from services.agent_orchestrator import AgentOrchestrator # the full 4-agent pipeline
from services.llm_service import LLMService               # shared — circuit breaker persists
from services.rag_service import RAGService               # shared — model loaded once
from config import settings                               # redis_url from .env

logger = logging.getLogger(__name__)
MAX_RETRIES = 2                        # attempt the pipeline up to 3 times total (1 + 2 retries)
RETRY_BACKOFF = [5, 15]               # seconds to wait before retry 1, retry 2


async def process_one(
    incident_id: str,
    llm_svc: LLMService,
    rag_svc: RAGService,
    redis_client: aioredis.Redis,
) -> None:
    """
    Run the 4-agent investigation pipeline for one incident, with retries.

    Retry policy:
      - Attempt 1: run immediately
      - Attempt 2: wait 5 seconds, try again  (transient LLM/DB hiccup)
      - Attempt 3: wait 15 seconds, try again (longer outage)
      - All failed → mark incident as "failed" so it doesn't stay "investigating"

    Why retry?
      Anthropic API has occasional transient 529 (overload) errors.
      A single retry catches ~95% of transient failures without adding
      significant delay for the common case (success on first attempt).
    """
    for attempt in range(1, MAX_RETRIES + 2):   # 1, 2, 3
        async with AsyncSessionLocal() as db:
            repo = IncidentRepository(db)
            incident = await repo.get_by_id(incident_id)

            if not incident:
                logger.warning("Incident not found — skipping", extra={"incident_id": incident_id})
                return

            try:
                ACTIVE_INVESTIGATIONS.inc()
                start = time.time()

                orchestrator = AgentOrchestrator(db, llm_svc, rag_svc)
                await orchestrator.run(incident)

                duration = time.time() - start
                INVESTIGATION_DURATION.observe(duration)
                INVESTIGATIONS_TOTAL.labels(status="resolved").inc()
                ACTIVE_INVESTIGATIONS.dec()

                logger.info(
                    "Investigation complete",
                    extra={"incident_id": incident_id, "attempt": attempt, "duration_seconds": round(duration, 2)}
                )
                return   # success — exit the retry loop

            except Exception as e:
                duration = time.time() - start
                INVESTIGATION_DURATION.observe(duration)   # record failed durations too — shows real tail latency
                ACTIVE_INVESTIGATIONS.dec()
                logger.warning(
                    "Investigation attempt failed",
                    extra={
                        "incident_id": str(incident_id),
                        "attempt": attempt,
                        "max_attempts": MAX_RETRIES + 1,
                        "error": str(e),
                    },
                    exc_info=True,
                )

                if attempt <= MAX_RETRIES:
                    wait = RETRY_BACKOFF[attempt - 1]
                    logger.info(
                        "Retrying investigation",
                        extra={"incident_id": incident_id, "wait_seconds": wait, "next_attempt": attempt + 1}
                    )
                    await asyncio.sleep(wait)
                else:
                    # All attempts exhausted — mark as failed in Postgres
                    # AND push to the dead letter queue so the job is never lost.
                    INVESTIGATIONS_TOTAL.labels(status="failed").inc()
                    logger.error(
                        "All retry attempts exhausted — moving to dead letter queue",
                        extra={"incident_id": incident_id, "attempts": attempt}
                    )
                    await repo.update_status(incident_id, "failed")
                    await db.commit()
                    payload = json.dumps({"incident_id": incident_id})
                    await redis_client.rpush(DEAD_KEY, payload)
                    logger.info(
                        "Incident moved to dead letter queue",
                        extra={"incident_id": incident_id, "dead_queue": DEAD_KEY}
                    )


async def main() -> None:
    # Configure JSON logging for the worker process.
    # The FastAPI process calls setup_logging() in main.py.
    # The worker is a separate process — it must configure logging independently.
    setup_logging(settings.log_level)

    logger.info("Starting Sentinel investigation worker", extra={"mode": "agent"})

    # ── Graceful shutdown setup ────────────────────────────────────────────────
    # asyncio.Event is a flag that can be set from a signal handler.
    # We use loop.add_signal_handler() (not signal.signal()) because:
    #   - signal.signal() handlers run in the main thread, which can block the
    #     event loop at an arbitrary point mid-coroutine
    #   - loop.add_signal_handler() schedules the callback safely on the event
    #     loop so it runs between coroutine steps, never corrupting state
    shutdown_event = asyncio.Event()

    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, shutdown_event.set)  # docker-compose down
    loop.add_signal_handler(signal.SIGINT,  shutdown_event.set)  # Ctrl+C locally

    # Create shared instances — once per worker lifetime, not per job.
    # LLMService: shared so the circuit breaker failure count persists across all jobs.
    # RAGService: shared because loading the embedding model takes ~2s — too slow per job.
    llm_svc = LLMService()
    rag_svc = RAGService()

    redis_client = await aioredis.from_url(settings.redis_url)
    logger.info("Connected to Redis — listening for alerts", extra={"queue": QUEUE_KEY})

    # ── Main loop ──────────────────────────────────────────────────────────────
    # Runs until SIGTERM/SIGINT sets shutdown_event.
    # BLPOP timeout=5 means the loop wakes up every 5 seconds even if the queue
    # is empty — this is what lets us check shutdown_event and exit promptly
    # instead of blocking forever waiting for a message that never comes.
    while not shutdown_event.is_set():
        result = await redis_client.blpop(QUEUE_KEY, timeout=5)

        if result is None:
            # Update queue depth gauges every 5s even when idle
            QUEUE_DEPTH.labels(queue="alert").set(await redis_client.llen(QUEUE_KEY))
            QUEUE_DEPTH.labels(queue="dead").set(await redis_client.llen(DEAD_KEY))
            continue    # timeout — queue was empty, check shutdown_event and loop

        _, raw = result                         # BLPOP returns (key, value) tuple
        payload = json.loads(raw)              # parse {"incident_id": "uuid-string"}
        incident_id = uuid.UUID(payload["incident_id"])   # convert to UUID — repo methods expect uuid.UUID

        logger.info("Received alert — starting agent pipeline", extra={"incident_id": incident_id})

        # process_one() can take 10–30 seconds (4 LLM calls).
        # If SIGTERM arrives during this call, shutdown_event is set but we
        # let process_one() finish. The `while not shutdown_event.is_set()`
        # check runs AFTER the job completes — not before or during.
        # This guarantees no investigation is abandoned half-finished.
        await process_one(incident_id, llm_svc, rag_svc, redis_client)

    # ── Clean exit ─────────────────────────────────────────────────────────────
    # We only reach here after shutdown_event is set AND the current job is done.
    logger.info("Shutdown signal received — worker exiting cleanly")
    await redis_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
