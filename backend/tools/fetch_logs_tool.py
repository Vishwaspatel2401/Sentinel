# =============================================================================
# FILE: backend/tools/fetch_logs_tool.py
# WHAT: Tool that fetches recent error logs for a service.
#       Wraps LogService so InvestigatorAgent can call it as a "tool action".
# WHY:  InvestigatorAgent doesn't call LogService directly — it calls tools.
#       This indirection means the agent loop is generic: it works with ANY tool
#       that follows the BaseTool interface. Adding a new data source = add a new
#       tool file, no changes to the agent.
# OOP:  Composition — FetchLogsTool HAS-A LogService. It delegates the actual
#       DB query to LogService and just formats the result as a string.
#       Inheritance — inherits the BaseTool interface (name, description, run).
# CONNECTED TO:
#   ← tools/base_tool.py         — inherits BaseTool interface
#   ← services/log_service.py    — delegates fetch + summarize here
#   → agents/investigator_agent.py — added to the agent's tools list
# =============================================================================

from tools.base_tool import BaseTool           # abstract interface all tools implement
from services.log_service import LogService    # does the actual DB query


class FetchLogsTool(BaseTool):

    def __init__(self, log_svc: LogService):
        # log_svc is injected — not created here.
        # Same LogService instance used by the rest of the pipeline,
        # so we reuse the same DB session rather than opening a new one.
        self.log_svc = log_svc

    @property
    def name(self) -> str:
        # This exact string must match what the LLM writes after "ACTION:".
        # The InvestigatorAgent does: next(t for t in tools if t.name == action_name)
        return "fetch_logs"

    @property
    def description(self) -> str:
        # This goes into the system prompt so the LLM knows what the tool does
        # and what input to provide.
        return (
            "Fetches recent ERROR logs for a service from the last 30 minutes. "
            "Input: {\"service_name\": \"<name>\"}. "
            "Returns a summary of the top error patterns and total error count."
        )

    async def run(self, input_data: dict) -> str:
        # Extract service_name from the LLM's parsed input.
        # If the LLM forgot to provide it, default to empty string (safe fallback).
        service_name = input_data.get("service_name", "")

        if not service_name:
            return "Error: service_name is required. Provide {\"service_name\": \"<name>\"}."

        # Fetch logs from DB via LogService
        logs = await self.log_svc.fetch_recent_errors(service_name)

        # Summarize — condensed string, not 847 raw lines
        # LogService.summarize() returns e.g. "847 total errors. Top patterns:\n847x: connection refused"
        summary = self.log_svc.summarize(logs)

        # Return the observation string — the LLM reads this on the next ReAct iteration
        return f"[fetch_logs result for '{service_name}']\n{summary}"
