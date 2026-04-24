# =============================================================================
# FILE: backend/services/alert_service.py
# WHAT: Coordinates LogService and DeployService to gather investigation
#       evidence in parallel using asyncio.gather.
# WHY:  Logs and deploys are independent — there's no reason to wait for logs
#       before starting the deploy fetch. Running both simultaneously cuts
#       evidence-gathering time in half (~300ms instead of ~600ms).
# OOP:  Composition pattern — AlertService HAS-A LogService and HAS-A DeployService.
#       It doesn't inherit from them (IS-A). It delegates to them.
#       This is more flexible than inheritance — you can swap either service
#       for a mock in tests without changing AlertService at all.
# CONNECTED TO:
#   ← services/log_service.py — called for error logs
#   ← services/deploy_service.py — called for deploy history
#   → services/investigation_service.py calls gather_evidence() to get
#     (logs, deploys) before building the LLM prompt
# =============================================================================

import asyncio                                          # for running tasks in parallel
from sqlalchemy.ext.asyncio import AsyncSession        # async DB session type
from services.log_service import LogService            # fetches error logs
from services.deploy_service import DeployService      # fetches deploy history
from db.models import LogEntry, Deploy                 # return types


# AlertService: coordinates LogService and DeployService to gather evidence in parallel.
# This is the entry point for the investigation data-gathering phase.
# OOP concept: Composition — AlertService HAS-A LogService and HAS-A DeployService.
# It doesn't inherit from them, it holds instances of them and delegates to them.
class AlertService:

    def __init__(self, db: AsyncSession):
        # Compose the two services — pass the same DB session to both.
        # Same session = same transaction = consistent view of the DB.
        self.log_svc = LogService(db)
        self.deploy_svc = DeployService(db)

    async def gather_evidence(
        self, service_name: str
    ) -> tuple[list[LogEntry], list[Deploy]]:
        # asyncio.gather runs both coroutines CONCURRENTLY — not one after the other.
        # Without gather: fetch logs (300ms) THEN fetch deploys (300ms) = 600ms total.
        # With gather:    fetch logs + fetch deploys at the same time = ~300ms total.
        # This is the distributed systems concept: parallelise independent I/O calls.
        logs, deploys = await asyncio.gather(
            self.log_svc.fetch_recent_errors(service_name),
            self.deploy_svc.fetch_recent_deploys(service_name),
        )

        return logs, deploys
