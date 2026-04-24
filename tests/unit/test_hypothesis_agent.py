# =============================================================================
# FILE: tests/unit/test_hypothesis_agent.py
# WHAT: Unit tests for HypothesisAgent — the agent that forms root cause
#       hypotheses from gathered evidence.
# WHY:  HypothesisAgent must:
#         1. Parse Claude's JSON response correctly
#         2. Write root_cause and confidence to context
#         3. Handle JSON parse failures gracefully (confidence → 0.0, not crash)
#         4. Always produce a float for confidence (not a string)
# =============================================================================

import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from agents.hypothesis_agent import HypothesisAgent


def make_agent(llm_response: str, mock_incident, evidence_summary="847x connection refused"):
    """Create HypothesisAgent with a mock LLM and pre-filled context."""
    llm_svc = MagicMock()
    llm_svc.call = AsyncMock(return_value=llm_response)

    context = {
        "incident": mock_incident,
        "incident_type": "db_issue",
        "evidence_summary": evidence_summary,
    }
    return HypothesisAgent(llm_svc=llm_svc, context=context)


class TestHypothesisAgent:

    async def test_extracts_root_cause_from_json(self, mock_incident):
        # Valid JSON response — root_cause should be written to context exactly.
        response = json.dumps({
            "root_cause": "pool_size reduced from 20 to 5 causing exhaustion",
            "confidence": 0.95,
            "reasoning": "Deploy changed pool_size and errors started immediately after"
        })
        agent = make_agent(response, mock_incident)
        context = await agent.run()

        assert context["root_cause"] == "pool_size reduced from 20 to 5 causing exhaustion"

    async def test_extracts_confidence_as_float(self, mock_incident):
        # confidence must always be a float — never a string like "0.95"
        response = json.dumps({
            "root_cause": "some cause",
            "confidence": 0.87,
            "reasoning": "evidence"
        })
        agent = make_agent(response, mock_incident)
        context = await agent.run()

        assert isinstance(context["confidence"], float)
        assert context["confidence"] == 0.87

    async def test_handles_json_parse_failure_gracefully(self, mock_incident):
        # If Claude returns prose instead of JSON, agent must not crash.
        # It should set confidence to 0.0 and a safe root_cause message.
        agent = make_agent("I think the database pool was exhausted.", mock_incident)
        context = await agent.run()

        assert context["confidence"] == 0.0
        assert "Unable to form hypothesis" in context["root_cause"]

    async def test_handles_markdown_wrapped_json(self, mock_incident):
        # Claude sometimes wraps JSON in ```json ... ``` fences.
        # BaseAgent._strip_markdown() handles this — verify it works end-to-end.
        inner = json.dumps({
            "root_cause": "pool exhausted",
            "confidence": 0.9,
            "reasoning": "deploy changed config"
        })
        response = f"```json\n{inner}\n```"

        agent = make_agent(response, mock_incident)
        context = await agent.run()

        assert context["root_cause"] == "pool exhausted"
        assert context["confidence"] == 0.9

    async def test_context_always_has_root_cause_after_run(self, mock_incident):
        # root_cause must always be set after run() — even on failure.
        # "None" would crash the database INSERT in the orchestrator.
        for bad_response in ["", "not json", "null", "[]"]:
            agent = make_agent(bad_response, mock_incident)
            context = await agent.run()
            assert context.get("root_cause") is not None
            assert context.get("confidence") is not None

    async def test_reasoning_written_to_context(self, mock_incident):
        # HypothesisAgent should also write "reasoning" to context
        # so ResponderAgent can use it to write a better fix.
        response = json.dumps({
            "root_cause": "pool exhausted",
            "confidence": 0.9,
            "reasoning": "The deploy timestamp exactly matches error spike onset"
        })
        agent = make_agent(response, mock_incident)
        context = await agent.run()

        assert "deploy timestamp" in context["reasoning"]
