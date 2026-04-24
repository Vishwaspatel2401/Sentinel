# =============================================================================
# FILE: backend/agents/classifier_agent.py
# WHAT: First agent in the pipeline. Reads the raw alert and classifies what
#       TYPE of incident this is before any investigation begins.
# WHY:  Knowing the incident type upfront helps downstream agents focus.
#       The InvestigatorAgent can prioritise which tools to call first:
#         db_issue        → fetch deploys first (most likely a config change)
#         memory_leak     → fetch logs first (look for OOM patterns)
#         network_issue   → fetch logs first (look for timeout patterns)
#         deploy_regression → fetch deploys immediately
#       Without classification, the agent would always use the same strategy
#       regardless of the incident type.
# FLOW:
#   1. Reads incident details from self.context["incident"]
#   2. Builds a prompt describing the alert
#   3. Calls Claude via think() — single-turn, expects one of 5 category strings
#   4. Parses the response, writes "incident_type" to self.context
# DESIGN PATTERNS:
#   Template Method — implements system_prompt() and run() as required by BaseAgent.
#                     BaseAgent's think() does the actual LLM call.
# OOP:
#   Inheritance     — inherits think(), _strip_markdown(), context from BaseAgent.
#   Single Responsibility — only classifies. Does NOT investigate or hypothesise.
# CONNECTED TO:
#   ← agents/base_agent.py             — inherits from BaseAgent
#   ← services/llm_service.py          — LLM call via self.think()
#   → services/agent_orchestrator.py   — creates and calls this agent first
#   → agents/investigator_agent.py     — reads "incident_type" from context
# =============================================================================

import logging
from agents.base_agent import BaseAgent          # abstract base — provides think(), context
from services.llm_service import LLMService      # type hint for constructor

logger = logging.getLogger(__name__)


class ClassifierAgent(BaseAgent):

    def __init__(self, llm_svc: LLMService, context: dict):
        # Call the parent constructor — stores llm_svc and context on self.
        # Always call super().__init__() first in subclass constructors.
        super().__init__(llm_svc, context)

    def system_prompt(self) -> str:
        # Tells Claude what role it plays and what exact output format to return.
        # Strict format (one of 5 words) = easy to parse without regex tricks.
        return """
You are an incident classification system for a production SRE platform.

Your job: read the incident details and classify it into exactly one category.

Categories:
  db_issue          — database errors, connection refused, query timeouts, pool exhaustion
  memory_leak       — OOMKilled, high memory usage, heap pressure, GC pauses
  network_issue     — timeouts, DNS failures, packet loss, certificate errors
  deploy_regression — errors started immediately after a deployment
  unknown           — cannot determine from available information

Rules:
  - Respond with ONLY the category name. No explanation, no punctuation, no extra text.
  - Example valid response: db_issue
  - Example invalid response: "I think this is a db_issue because..."
"""

    async def run(self) -> dict:
        # Read the incident from the shared blackboard context.
        # The orchestrator puts the incident there before calling the first agent.
        incident = self.context["incident"]

        # Build a descriptive prompt from the incident fields.
        # More context = better classification. Include title, description, and error_type.
        prompt = f"""
Classify this production incident:

  Service:     {incident.service_name}
  Severity:    {incident.severity}
  Title:       {incident.title}
  Description: {incident.description}
  Error type:  {incident.error_type}
  Source:      {incident.source}

Respond with one category name only.
"""

        # Call Claude via BaseAgent.think() — single turn, gets the classification back.
        response = await self.think(prompt)

        # Clean up the response — strip whitespace and lowercase it.
        # Claude might return "DB_Issue" or " db_issue " — normalise both.
        incident_type = response.strip().lower()

        # Validate — if Claude returned something unexpected, default to "unknown"
        valid_types = {"db_issue", "memory_leak", "network_issue", "deploy_regression", "unknown"}
        if incident_type not in valid_types:
            logger.warning(
                "Unexpected classification — defaulting to 'unknown'",
                extra={"raw_response": incident_type}
            )
            incident_type = "unknown"

        # Write result to the blackboard — InvestigatorAgent will read this.
        self.context["incident_type"] = incident_type

        logger.info("Incident classified", extra={"incident_type": incident_type})
        return self.context
