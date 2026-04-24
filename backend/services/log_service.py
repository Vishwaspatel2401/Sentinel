# =============================================================================
# FILE: backend/services/log_service.py
# WHAT: Fetches and summarises recent error logs for a given service.
# WHY:  In production, logs live in Datadog/CloudWatch/Loki — this service
#       is the abstraction layer. Swap the DB query for an API call and
#       nothing else in the app changes.
#       The summarize() method condenses 100 log lines into ~5 lines before
#       sending to the LLM — saves tokens and money.
# OOP:  Single Responsibility — fetch() fetches, summarize() formats.
#       Two separate jobs, two separate methods.
# CONNECTED TO:
#   ← db/models.py provides the LogEntry ORM model
#   ← db/database.py provides AsyncSession (injected via AlertService)
#   → services/alert_service.py calls fetch_recent_errors() via asyncio.gather
#   → services/investigation_service.py passes the summary to LLMService
# =============================================================================

from datetime import datetime, timedelta, timezone     # for calculating the time window
from sqlalchemy.ext.asyncio import AsyncSession        # async DB session type
from sqlalchemy import select, func                    # query builder + SQL functions
from db.models import LogEntry                         # the ORM model for the log_entries table


# LogService: fetches recent error logs for a given service.
# In production this would call Datadog, CloudWatch, or Loki APIs.
# For the portfolio, we query mock data seeded into the log_entries table.
class LogService:

    def __init__(self, db: AsyncSession):
        self.db = db                                   # store session for use in methods

    async def fetch_recent_errors(self, service_name: str, window_minutes: int = 30) -> list[LogEntry]:
        # Calculate the start of the time window — e.g. 30 minutes ago
        since = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)

        result = await self.db.execute(
            select(LogEntry)
            .where(LogEntry.service_name == service_name)  # only this service's logs
            .where(LogEntry.level == "ERROR")              # only error-level logs
            .where(LogEntry.timestamp >= since)            # only within the time window
            .order_by(LogEntry.timestamp.desc())           # most recent first
            .limit(100)                                    # cap at 100 — don't flood the LLM context
        )

        return result.scalars().all()                  # returns a list of LogEntry objects

    def summarize(self, logs: list[LogEntry]) -> str:
        # Summarize raw logs into a short string before passing to the LLM.
        # Sending 100 full log lines to an LLM is wasteful — summarize first.
        # This is cost control: fewer tokens = cheaper API calls.
        if not logs:
            return "No recent errors found."

        # Count how many times each unique message appears
        counts: dict[str, int] = {}
        for log in logs:
            counts[log.message] = counts.get(log.message, 0) + 1

        # Sort by frequency — most common error first
        sorted_errors = sorted(counts.items(), key=lambda x: x[1], reverse=True)

        # Take top 5 most frequent errors only
        top = sorted_errors[:5]
        lines = [f"{count}x: {message}" for message, count in top]

        return f"{len(logs)} total errors. Top patterns:\n" + "\n".join(lines)
