# =============================================================================
# FILE: tests/unit/test_classifier_agent.py
# WHAT: Unit tests for ClassifierAgent — the first agent in the pipeline.
# WHY:  ClassifierAgent determines the incident_type that guides the entire
#       investigation. If it classifies wrong, the InvestigatorAgent may use
#       the wrong strategy. We need to verify:
#         1. Valid LLM responses are correctly stored in context
#         2. Invalid/unexpected responses default to "unknown" safely
#         3. The context is always updated (never left None after run())
# KEY CONCEPT — Mocking agents:
#   Agents call the LLM via self.llm_svc.call(). In tests we swap the real
#   LLMService for a mock where we control exactly what call() returns.
#   This lets us test the agent's LOGIC (parsing, validation, context writes)
#   without spending money on API calls or waiting for network responses.
# =============================================================================

import pytest
from unittest.mock import AsyncMock, MagicMock

from agents.classifier_agent import ClassifierAgent


class TestClassifierAgent:

    def make_agent(self, llm_response: str, mock_incident):
        """Helper: create a ClassifierAgent with a mock LLM returning llm_response."""
        llm_svc = MagicMock()
        # AsyncMock because ClassifierAgent does: response = await self.think(prompt)
        # A regular MagicMock is not awaitable — it would raise TypeError.
        llm_svc.call = AsyncMock(return_value=llm_response)

        context = {"incident": mock_incident}
        return ClassifierAgent(llm_svc=llm_svc, context=context)

    async def test_classifies_db_issue(self, mock_incident):
        # When the LLM returns "db_issue", context["incident_type"] should be "db_issue".
        agent = self.make_agent("db_issue", mock_incident)
        context = await agent.run()
        assert context["incident_type"] == "db_issue"

    async def test_classifies_memory_leak(self, mock_incident):
        agent = self.make_agent("memory_leak", mock_incident)
        context = await agent.run()
        assert context["incident_type"] == "memory_leak"

    async def test_classifies_network_issue(self, mock_incident):
        agent = self.make_agent("network_issue", mock_incident)
        context = await agent.run()
        assert context["incident_type"] == "network_issue"

    async def test_classifies_deploy_regression(self, mock_incident):
        agent = self.make_agent("deploy_regression", mock_incident)
        context = await agent.run()
        assert context["incident_type"] == "deploy_regression"

    async def test_unknown_classification_for_garbage_response(self, mock_incident):
        # If Claude returns something unexpected (hallucination, wrong format),
        # the agent must default to "unknown" — never crash, never leave None.
        agent = self.make_agent("I think this might be a database problem!", mock_incident)
        context = await agent.run()
        assert context["incident_type"] == "unknown"

    async def test_strips_whitespace_from_response(self, mock_incident):
        # Claude might return " db_issue\n" — leading/trailing whitespace must be stripped.
        agent = self.make_agent("  db_issue  \n", mock_incident)
        context = await agent.run()
        assert context["incident_type"] == "db_issue"

    async def test_handles_uppercase_response(self, mock_incident):
        # Claude might return "DB_ISSUE" — we normalise to lowercase before validation.
        agent = self.make_agent("DB_ISSUE", mock_incident)
        context = await agent.run()
        # "db_issue" is valid after lowercasing
        assert context["incident_type"] == "db_issue"

    async def test_incident_type_never_none_after_run(self, mock_incident):
        # Whatever the LLM returns, incident_type must always be set after run().
        # "None" in the context would silently break InvestigatorAgent's strategy logic.
        for response in ["gibberish", "", "   ", "unknown", "db_issue"]:
            agent = self.make_agent(response, mock_incident)
            context = await agent.run()
            assert context["incident_type"] is not None
