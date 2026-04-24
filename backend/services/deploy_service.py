# =============================================================================
# FILE: backend/services/deploy_service.py
# WHAT: Fetches recent deployment history for a given service.
# WHY:  The most common cause of production incidents is a bad deploy.
#       This service finds deploys that happened in the 2 hours before an alert —
#       the investigation engine checks if one of them caused the issue.
#       In production this would call GitHub Actions, Spinnaker, or ArgoCD APIs.
#       For the portfolio, we query mock data seeded into the deploys table.
# OOP:  Single Responsibility — only fetches deploys, nothing else.
#       Encapsulation — DB query details hidden inside the class.
# CONNECTED TO:
#   ← db/models.py provides the Deploy ORM model
#   ← db/database.py provides AsyncSession (injected via AlertService)
#   → services/alert_service.py calls fetch_recent_deploys() via asyncio.gather
#   → services/investigation_service.py includes deploy data in the LLM prompt
# =============================================================================

from datetime import datetime, timedelta, timezone     # for calculating the time window
from sqlalchemy.ext.asyncio import AsyncSession        # async DB session type
from sqlalchemy import select                          # SQLAlchemy query builder
from db.models import Deploy                           # the ORM model for the deploys table


# DeployService: fetches deployment history for a given service.
# In production this would call a deploy pipeline API (GitHub Actions, Spinnaker).
# For the portfolio, we query mock data seeded into the deploys table.
class DeployService:

    def __init__(self, db: AsyncSession):
        self.db = db                                   # store session for use in methods

    async def fetch_recent_deploys(self, service_name: str, hours: int = 2) -> list[Deploy]:
        # Calculate the start of the time window — e.g. 2 hours ago
        since = datetime.now(timezone.utc) - timedelta(hours=hours)

        result = await self.db.execute(
            select(Deploy)
            .where(Deploy.service_name == service_name)   # only this service's deploys
            .where(Deploy.deployed_at >= since)            # only within the time window
            .order_by(Deploy.deployed_at.desc())           # most recent first
        )

        return result.scalars().all()                  # returns a list of Deploy objects
