# =============================================================================
# FILE: tests/unit/test_responder_agent.py
# WHAT: Unit tests for ResponderAgent — the final agent that writes the fix
#       and decides whether to escalate to a human.
# WHY:  ResponderAgent has a critical hard rule:
#           confidence < 0.5 → ALWAYS escalate, regardless of what the LLM says.
#       This is a safety guarantee. If we break it, low-confidence analyses
#       could tell engineers to apply risky fixes without human verification.
#       We test the hard rule separately from the LLM response parsing.
# WHAT WE TEST:
#   1. High confidence + LLM says no escalate → no escalate
#   2. Low confidence + LLM says no escalate → HARD RULE forces escalate
#   3. High confidence + LLM says escalate   → escalate (LLM is right)
#   4. JSON parse failures → safe defaults (escalate=True)
#   5. Evidence list is written to context correctly
# =============================================================================

import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from agents.responder_agent import ResponderAgent


def make_agent(llm_response: str, mock_incident, confidence: float = 0.9):
    """Create ResponderAgent with a mock LLM and context pre-filled with hypothesis."""
    llm_svc = MagicMock()
    llm_svc.call = AsyncMock(return_value=llm_response)

    context = {
        "incident": mock_incident,
        "incident_type": "db_issue",
        "root_cause": "pool_size reduced from 20 to 5",
        "confidence": confidence,
        "reasoning": "deploy timestamp matches error spike",
        "evidence_summary": "100x connection refused, v2.4.1 deploy changed pool_size",
    }
    return ResponderAgent(llm_svc=llm_svc, context=context)


def valid_llm_response(escalate: bool = False) -> str:
    return json.dumps({
        "suggested_fix": "1. kubectl rollout undo\n2. monitor for 5 min",
        "escalate": escalate,
        "escalation_reason": "confidence is high" if not escalate else "risky fix",
        "evidence": ["100x connection refused", "pool_size changed 20→5"]
    })


class TestResponderAgent:

    async def test_high_confidence_no_escalation(self, mock_incident):
        # confidence=0.9, LLM says escalate=False → should NOT escalate
        agent = make_agent(valid_llm_response(escalate=False), mock_incident, confidence=0.9)
        context = await agent.run()

        assert context["escalate"] is False

    async def test_low_confidence_forces_escalation(self, mock_incident):
        # THIS IS THE CRITICAL TEST.
        # confidence=0.3 (below 0.5 threshold), LLM says escalate=False
        # → Hard rule must OVERRIDE the LLM and set escalate=True
        agent = make_agent(valid_llm_response(escalate=False), mock_incident, confidence=0.3)
        context = await agent.run()

        assert context["escalate"] is True
        # The escalation reason should mention the hard rule / threshold
        assert "50%" in context["escalation_reason"] or "confidence" in context["escalation_reason"].lower()

    async def test_llm_escalate_true_respected(self, mock_incident):
        # LLM decides to escalate (risky fix) even with high confidence.
        # We respect the LLM's escalation — only override in the low-confidence direction.
        agent = make_agent(valid_llm_response(escalate=True), mock_incident, confidence=0.9)
        context = await agent.run()

        assert context["escalate"] is True

    async def test_suggested_fix_written_to_context(self, mock_incident):
        agent = make_agent(valid_llm_response(), mock_incident)
        context = await agent.run()

        assert "kubectl rollout undo" in context["suggested_fix"]

    async def test_evidence_list_written_to_context(self, mock_incident):
        agent = make_agent(valid_llm_response(), mock_incident)
        context = await agent.run()

        assert isinstance(context["evidence"], list)
        assert len(context["evidence"]) == 2
        assert "100x connection refused" in context["evidence"]

    async def test_json_parse_failure_defaults_to_escalate(self, mock_incident):
        # If Claude returns prose instead of JSON, we can't parse the fix.
        # The safe default is escalate=True — never auto-apply an unknown fix.
        agent = make_agent("Sorry, I cannot determine the fix.", mock_incident)
        context = await agent.run()

        assert context["escalate"] is True
        assert context["suggested_fix"] != ""   # should still have a message

    async def test_boundary_confidence_exactly_05_does_not_escalate(self, mock_incident):
        # The hard rule is confidence < 0.5 (strictly less than).
        # confidence = 0.5 exactly should NOT trigger auto-escalation.
        agent = make_agent(valid_llm_response(escalate=False), mock_incident, confidence=0.5)
        context = await agent.run()

        assert context["escalate"] is False

    async def test_boundary_confidence_0499_escalates(self, mock_incident):
        # 0.499 < 0.5 → must escalate
        agent = make_agent(valid_llm_response(escalate=False), mock_incident, confidence=0.499)
        context = await agent.run()

        assert context["escalate"] is True
