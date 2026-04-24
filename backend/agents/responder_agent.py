# =============================================================================
# FILE: backend/agents/responder_agent.py
# WHAT: Fourth and final agent. Takes the root cause hypothesis and produces
#       an actionable fix + escalation decision for the on-call engineer.
# WHY:  Separating the FIX from the ROOT CAUSE matters because:
#         1. The fix depends on confidence — low confidence = "investigate manually"
#         2. Escalation logic (page a human?) is a policy decision, not a hypothesis
#         3. If you change escalation rules, you change only this file
#       A single LLM call that does everything can't be tuned this precisely.
# FLOW:
#   1. Reads "root_cause", "confidence", "evidence_summary" from context
#   2. Builds a prompt with the full picture
#   3. Calls Claude via think() — expects JSON with suggested_fix + escalate
#   4. Applies a hard rule: confidence < 0.5 ALWAYS escalates (overrides LLM)
#   5. Writes "suggested_fix" and "escalate" to context
# DESIGN PATTERNS:
#   Template Method — implements system_prompt() and run() from BaseAgent.
# OOP:
#   Inheritance     — inherits think() from BaseAgent.
#   Single Responsibility — ONLY produces fixes and escalation decisions.
# CONNECTED TO:
#   ← agents/base_agent.py          — inherits BaseAgent
#   ← agents/hypothesis_agent.py    — reads "root_cause" + "confidence" from context
#   → services/agent_orchestrator.py — creates and calls this agent last
# =============================================================================

import json                              # for parsing Claude's JSON fix
import logging
from agents.base_agent import BaseAgent
from services.llm_service import LLMService

logger = logging.getLogger(__name__)


class ResponderAgent(BaseAgent):

    def __init__(self, llm_svc: LLMService, context: dict):
        super().__init__(llm_svc, context)

    def system_prompt(self) -> str:
        return """
You are a senior SRE writing an incident response for an engineer woken at 3 AM.

You will receive:
  - The confirmed root cause and confidence score
  - The full evidence summary
  - The incident details

Your job: write a practical fix and decide if human escalation is needed.

Respond ONLY with valid JSON in this exact format — no extra text, no markdown:
{
    "suggested_fix": "numbered step-by-step fix with exact commands",
    "escalate": false,
    "escalation_reason": "why escalation is or is not needed",
    "evidence": ["key evidence point 1", "key evidence point 2", "key evidence point 3"]
}

Rules:
  - suggested_fix must be actionable — include exact kubectl, git, or config commands
  - escalate must be true if confidence < 0.5, the fix is risky, or data loss is possible
  - evidence must quote specific numbers/versions from the investigation — never fabricate
  - Write for someone who just woke up — be direct, not verbose
"""

    async def run(self) -> dict:
        incident = self.context["incident"]
        root_cause = self.context.get("root_cause", "Unknown")
        confidence = self.context.get("confidence", 0.0)
        reasoning = self.context.get("reasoning", "")
        evidence_summary = self.context.get("evidence_summary", "")
        incident_type = self.context.get("incident_type", "unknown")

        prompt = f"""
Write an incident response for this confirmed root cause:

INCIDENT:
  Service:    {incident.service_name}
  Severity:   {incident.severity}
  Title:      {incident.title}
  Type:       {incident_type}

ROOT CAUSE HYPOTHESIS:
  Root cause:  {root_cause}
  Confidence:  {confidence:.0%}
  Reasoning:   {reasoning}

EVIDENCE SUMMARY:
{evidence_summary}

Write a practical fix for the on-call engineer and decide if escalation is needed.
Return your response as JSON.
"""

        response = await self.think(prompt)

        # Parse JSON from Claude's response
        try:
            result = json.loads(response)
        except json.JSONDecodeError:
            logger.warning("JSON parse failed — forcing escalation", extra={"raw_preview": response[:200]})
            result = {
                "suggested_fix": "Unable to generate fix. Investigate manually.",
                "escalate": True,
                "escalation_reason": "JSON parse failed — manual review required.",
                "evidence": [],
            }

        # ── Hard rule: always escalate if confidence is low ────────────────────
        # This is a business rule, not an LLM decision.
        # We override whatever the LLM said about escalation if confidence < 0.5.
        # Low confidence means the agent isn't sure — a human MUST verify.
        # This prevents the system from auto-applying risky fixes with low certainty.
        if confidence < 0.5 and not result.get("escalate", False):
            result["escalate"] = True
            result["escalation_reason"] = (
                f"Auto-escalated: confidence {confidence:.0%} is below 50% threshold. "
                "Human verification required before applying fix."
            )

        # Write all final results to the blackboard.
        # These are what get saved to the Resolution table in Postgres.
        self.context["suggested_fix"] = result.get("suggested_fix", "No fix generated.")
        self.context["escalate"] = result.get("escalate", True)
        self.context["escalation_reason"] = result.get("escalation_reason", "")
        self.context["evidence"] = result.get("evidence", [])

        logger.info(
            "Fix generated",
            extra={
                "escalate": self.context["escalate"],
                "escalation_reason": self.context["escalation_reason"][:80],
            }
        )
        return self.context
