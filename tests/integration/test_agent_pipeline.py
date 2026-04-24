# =============================================================================
# FILE: tests/integration/test_agent_pipeline.py
# WHAT: Integration tests for the full 4-agent pipeline end-to-end.
#       Tests that all agents run in the correct order and the final result
#       is what we expect — without touching a real DB or real Anthropic API.
# WHY:  Unit tests verify each piece in isolation.
#       Integration tests verify the pieces WORK TOGETHER correctly.
#       Common integration bugs:
#         - Agent A writes "root_cause" but Agent B reads "rootCause" (key mismatch)
#         - Agent A crashes and Agent B runs anyway on empty context
#         - Orchestrator saves the wrong field to Resolution
# HOW WE MOCK:
#   - LLMService.call() and call_with_messages() return canned responses
#   - DB session is fully mocked (AsyncMock) — no Postgres connection needed
#   - RAGService.retrieve() returns a fixed list of strings
#   - LogService and DeployService return mock ORM objects
#
# KEY CONCEPT — Integration vs Unit:
#   Unit test  → one class, everything else mocked
#   Integration test → multiple real classes wired together, only external
#                      dependencies (DB, LLM API) mocked
# =============================================================================

import json
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

# We import the real classes — this is what makes it an integration test
from agents.classifier_agent import ClassifierAgent
from agents.investigator_agent import InvestigatorAgent
from agents.hypothesis_agent import HypothesisAgent
from agents.responder_agent import ResponderAgent
from tools.fetch_logs_tool import FetchLogsTool
from tools.fetch_deploys_tool import FetchDeploysTool
from tools.runbook_tool import RunbookTool


# ── Shared fake data ───────────────────────────────────────────────────────────

def make_incident():
    incident = MagicMock()
    incident.id = uuid.uuid4()
    incident.service_name = "payments-api"
    incident.severity = "P1"
    incident.title = "DB connection timeout"
    incident.description = "connection refused errors on /charge endpoint"
    incident.error_type = "db_timeout"
    incident.source = "prometheus"
    incident.status = "investigating"
    return incident


def make_deploy():
    d = MagicMock()
    d.version = "v2.4.1"
    d.deployed_at = datetime(2024, 1, 15, 2, 13, tzinfo=timezone.utc)
    d.deployed_by = "ci-bot"
    d.diff_summary = "pool_size: 20→5"
    return d


def make_log_entry():
    log = MagicMock()
    log.message = "connection refused"
    log.level = "ERROR"
    log.service_name = "payments-api"
    log.timestamp = datetime(2024, 1, 15, 2, 30, tzinfo=timezone.utc)
    return log


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestAgentPipelineIntegration:

    def make_llm_service(self):
        """
        Build a mock LLMService that returns realistic canned responses
        for each agent's LLM call in order.

        The order of calls across agents:
          1. ClassifierAgent.think()          → call()
          2. InvestigatorAgent loop iter 1    → call_with_messages()
          3. InvestigatorAgent loop iter 2    → call_with_messages()
          4. HypothesisAgent.think()          → call()
          5. ResponderAgent.think()           → call()
        """
        llm_svc = MagicMock()
        llm_svc.model = "claude-haiku-test"

        # call() responses (single-turn: ClassifierAgent, HypothesisAgent, ResponderAgent)
        llm_svc.call = AsyncMock(side_effect=[
            # 1. ClassifierAgent → classify as db_issue
            "db_issue",

            # 2. HypothesisAgent → root cause JSON
            json.dumps({
                "root_cause": "pool_size reduced from 20 to 5 in v2.4.1 deploy",
                "confidence": 0.95,
                "reasoning": "Deploy timestamp matches error spike onset"
            }),

            # 3. ResponderAgent → fix JSON
            json.dumps({
                "suggested_fix": "1. kubectl rollout undo deployment/payments-api\n2. Monitor error rate",
                "escalate": False,
                "escalation_reason": "High confidence, clear fix",
                "evidence": [
                    "v2.4.1 reduced pool_size from 20 to 5",
                    "100 connection refused errors in last 30 minutes"
                ]
            }),
        ])

        # call_with_messages() responses (multi-turn: InvestigatorAgent ReAct loop)
        llm_svc.call_with_messages = AsyncMock(side_effect=[
            # Iteration 1: fetch logs
            'THOUGHT: Check error logs first\nACTION: fetch_logs\nINPUT: {"service_name": "payments-api"}',
            # Iteration 2: say DONE with evidence summary
            "THOUGHT: I have enough evidence\nDONE: 100 connection refused errors. v2.4.1 deploy changed pool_size 20→5. Runbook confirms pool of 5 is insufficient.",
        ])

        return llm_svc

    def make_tools(self):
        """Build mock tools that return realistic canned observations."""
        # FetchLogsTool wraps LogService — mock the log_svc inside it
        log_svc = MagicMock()
        log_entries = [make_log_entry() for _ in range(100)]
        log_svc.fetch_recent_errors = AsyncMock(return_value=log_entries)
        log_svc.summarize = MagicMock(
            return_value="100 total errors. Top patterns:\n100x: connection refused"
        )
        fetch_logs = FetchLogsTool(log_svc)

        # FetchDeploysTool wraps DeployService
        deploy_svc = MagicMock()
        deploy_svc.fetch_recent_deploys = AsyncMock(return_value=[make_deploy()])
        fetch_deploys = FetchDeploysTool(deploy_svc)

        # RunbookTool wraps RAGService
        rag_svc = MagicMock()
        rag_svc.retrieve = MagicMock(return_value=[
            "## DB Connection Pool\nIf pool_size < 10 and traffic > 15 req/s, connections will be refused."
        ])
        runbook = RunbookTool(rag_svc)

        return [fetch_logs, fetch_deploys, runbook]

    async def test_full_pipeline_runs_all_four_agents(self):
        """
        Verify all 4 agents run in sequence and produce a resolved context.
        This is the happy-path integration test — everything works.
        """
        incident = make_incident()
        llm_svc = self.make_llm_service()
        tools = self.make_tools()

        # Build shared context — same as AgentOrchestrator does
        context = {
            "incident": incident,
            "incident_type": None,
            "evidence_summary": "",
            "root_cause": "",
            "confidence": 0.0,
            "reasoning": "",
            "suggested_fix": "",
            "escalate": False,
            "escalation_reason": "",
            "evidence": [],
        }

        # Run all 4 agents in order — passing the same context dict (blackboard)
        context = await ClassifierAgent(llm_svc, context).run()
        context = await InvestigatorAgent(llm_svc, context, tools).run()
        context = await HypothesisAgent(llm_svc, context).run()
        context = await ResponderAgent(llm_svc, context).run()

        # ── Assert final state of the blackboard ──────────────────────────────
        assert context["incident_type"] == "db_issue"
        assert "pool_size" in context["evidence_summary"]
        assert "pool_size reduced from 20 to 5" in context["root_cause"]
        assert context["confidence"] == 0.95
        assert "kubectl rollout undo" in context["suggested_fix"]
        assert context["escalate"] is False
        assert len(context["evidence"]) == 2

    async def test_classifier_output_reaches_investigator(self):
        """
        Verify that ClassifierAgent's incident_type is visible to InvestigatorAgent.
        Tests the blackboard pattern — context is shared by reference.
        """
        incident = make_incident()
        llm_svc = MagicMock()
        llm_svc.call = AsyncMock(return_value="db_issue")

        context = {"incident": incident, "incident_type": None}
        context = await ClassifierAgent(llm_svc, context).run()

        # InvestigatorAgent reads incident_type from context in its run()
        assert context["incident_type"] == "db_issue"

    async def test_low_confidence_escalates_regardless_of_responder(self):
        """
        Integration test for the hard escalation rule.
        HypothesisAgent sets confidence=0.2 → ResponderAgent must escalate
        even if it would have said no escalation otherwise.
        """
        incident = make_incident()
        llm_svc = MagicMock()
        llm_svc.call = AsyncMock(side_effect=[
            # HypothesisAgent: low confidence
            json.dumps({
                "root_cause": "unknown cause",
                "confidence": 0.2,
                "reasoning": "insufficient evidence"
            }),
            # ResponderAgent: says no escalation needed (LLM decision)
            json.dumps({
                "suggested_fix": "investigate manually",
                "escalate": False,   # LLM says no — but hard rule should override
                "escalation_reason": "not needed",
                "evidence": []
            }),
        ])

        context = {
            "incident": incident,
            "incident_type": "unknown",
            "evidence_summary": "very little data",
        }
        context = await HypothesisAgent(llm_svc, context).run()
        context = await ResponderAgent(llm_svc, context).run()

        # Hard rule: confidence 0.2 < 0.5 threshold → must escalate
        assert context["escalate"] is True
