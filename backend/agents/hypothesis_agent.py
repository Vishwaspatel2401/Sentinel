# =============================================================================
# FILE: backend/agents/hypothesis_agent.py
# WHAT: Third agent in the pipeline. Takes the evidence gathered by
#       InvestigatorAgent and produces a structured root cause hypothesis.
# WHY:  Separating "gather evidence" (InvestigatorAgent) from "form hypothesis"
#       (HypothesisAgent) follows the Single Responsibility principle.
#       InvestigatorAgent decides WHAT to look at. HypothesisAgent decides
#       WHAT IT MEANS. Keeping them separate means you can swap the hypothesis
#       logic without changing how evidence is gathered.
# FLOW:
#   1. Reads "incident" and "evidence_summary" from context (written by InvestigatorAgent)
#   2. Builds a prompt with all the evidence
#   3. Calls Claude via think() — expects JSON back with root_cause + confidence
#   4. Parses the JSON, writes "root_cause" and "confidence" to context
# DESIGN PATTERNS:
#   Template Method — implements system_prompt() and run() as required by BaseAgent.
# OOP:
#   Inheritance     — inherits think(), _strip_markdown(), context from BaseAgent.
#   Single Responsibility — ONLY forms hypotheses. Does not gather evidence or fix.
# CONNECTED TO:
#   ← agents/base_agent.py           — inherits BaseAgent
#   ← agents/investigator_agent.py   — reads "evidence_summary" from context
#   → agents/responder_agent.py      — reads "root_cause" + "confidence" from context
#   → services/agent_orchestrator.py — creates and calls this agent third
# =============================================================================

import json                              # for parsing Claude's JSON hypothesis
import logging
from agents.base_agent import BaseAgent
from services.llm_service import LLMService

logger = logging.getLogger(__name__)


class HypothesisAgent(BaseAgent):

    def __init__(self, llm_svc: LLMService, context: dict):
        super().__init__(llm_svc, context)

    def system_prompt(self) -> str:
        return """
You are a senior SRE forming a root cause hypothesis from gathered evidence.

You will receive:
  - Incident details (service, severity, title, description)
  - Evidence summary from investigation (logs, deploys, runbook context)

Your job: analyse ALL the evidence and produce a structured hypothesis.

Respond ONLY with valid JSON in this exact format — no extra text, no markdown:
{
    "root_cause": "one clear sentence explaining what failed and why",
    "confidence": 0.87,
    "reasoning": "2-3 sentences explaining HOW you connected the evidence to this conclusion"
}

Rules:
  - confidence must be a float between 0.0 and 1.0
  - root_cause must be specific — include service names, version numbers, config values
  - reasoning must reference actual evidence — never fabricate data
  - If evidence is insufficient, set confidence below 0.3 and say so in root_cause
"""

    async def run(self) -> dict:
        incident = self.context["incident"]
        evidence_summary = self.context.get("evidence_summary", "No evidence gathered.")
        incident_type = self.context.get("incident_type", "unknown")

        # Build the prompt — combines incident context with the evidence the
        # InvestigatorAgent gathered. Claude gets everything it needs to reason.
        prompt = f"""
Form a root cause hypothesis for this incident:

INCIDENT:
  Service:        {incident.service_name}
  Severity:       {incident.severity}
  Title:          {incident.title}
  Description:    {incident.description}
  Classification: {incident_type}

EVIDENCE GATHERED BY INVESTIGATION:
{evidence_summary}

Analyse the evidence above and return your hypothesis as JSON.
"""

        # Single-turn call — BaseAgent.think() handles the LLM call + markdown stripping.
        response = await self.think(prompt)

        # Parse the JSON response.
        # Two failure modes to handle:
        #   1. json.loads() raises JSONDecodeError  — response is not valid JSON at all
        #   2. json.loads() succeeds but returns None or [] — "null" and "[]" are valid
        #      JSON but not dicts. Calling .get() on None/list crashes with AttributeError.
        # We handle both by checking isinstance(result, dict) after parsing.
        try:
            parsed = json.loads(response)
            if not isinstance(parsed, dict):
                # Valid JSON but wrong type (null, list, string, number)
                raise ValueError(f"Expected a JSON object, got {type(parsed).__name__}")
            result = parsed
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(
                "JSON parse failed — using safe defaults",
                extra={"error": str(e), "raw_preview": response[:200]}
            )
            # Safe defaults — low confidence signals the engineer to investigate manually
            result = {
                "root_cause": "Unable to form hypothesis from available evidence.",
                "confidence": 0.0,
                "reasoning": "JSON parse failed — Claude response was not valid JSON.",
            }

        # Write hypothesis to the blackboard.
        # ResponderAgent reads both "root_cause" and "confidence" to craft the fix.
        self.context["root_cause"] = result.get("root_cause", "Unknown")
        self.context["confidence"] = float(result.get("confidence", 0.0))
        self.context["reasoning"] = result.get("reasoning", "")

        logger.info(
            "Hypothesis formed",
            extra={
                "root_cause_preview": self.context["root_cause"][:80],
                "confidence": round(self.context["confidence"], 2),
            }
        )
        return self.context
