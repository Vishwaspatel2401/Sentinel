# =============================================================================
# FILE: tests/eval/test_llm_quality.py
# WHAT: LLM evaluation tests — checks the QUALITY of AI outputs, not just
#       whether the code runs without crashing.
# WHY:  Unit tests verify that ClassifierAgent calls the LLM and stores the result.
#       Eval tests verify that Claude actually returns "db_issue" for a DB incident.
#       These are different questions. Code can be perfectly correct but the
#       prompts can be bad — evals catch prompt regressions.
# DIFFERENCE FROM UNIT TESTS:
#   Unit test  → mock LLM → test code logic
#   Eval test  → real LLM → test AI output quality
# IMPORTANT:
#   These tests call the real Anthropic API — they cost money and take ~30 seconds.
#   DO NOT run these on every commit. Run them:
#     - Before changing a system prompt
#     - After changing a system prompt (verify it didn't regress)
#     - Nightly in CI if budget allows
#   Run with: backend/venv/bin/python -m pytest tests/eval/ -v
#   Skip in normal runs: backend/venv/bin/python -m pytest tests/unit/ tests/integration/ -v
# WHAT WE EVALUATE:
#   1. ClassifierAgent correctly classifies known incident types
#   2. HypothesisAgent produces high confidence for clear-cut scenarios
#   3. ResponderAgent always includes actionable commands in the fix
#   4. Full pipeline root cause mentions key evidence (pool_size, deploy version)
# =============================================================================

import json
import os
import pytest
from unittest.mock import MagicMock

# Mark every test in this file with the "eval" marker.
# This does two things:
#   1. CI runs `pytest -m "not eval"` — these tests are excluded automatically
#   2. Locally you can run `pytest -m eval` to run ONLY these tests
#
# We also keep a skipif guard for when someone runs `pytest` locally without a
# real key (e.g. a fresh clone before setting up .env).
pytestmark = [
    pytest.mark.eval,
    pytest.mark.skipif(
        not os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_API_KEY", "").startswith("test-"),
        reason="Real ANTHROPIC_API_KEY required — set it in .env to run eval tests"
    ),
]

from services.llm_service import LLMService
from agents.classifier_agent import ClassifierAgent
from agents.hypothesis_agent import HypothesisAgent
from agents.responder_agent import ResponderAgent


# ── Shared setup ───────────────────────────────────────────────────────────────

def make_real_llm():
    """Create a real LLMService that calls the actual Anthropic API."""
    return LLMService()


def make_incident(error_type="db_timeout", description="connection refused on /charge"):
    incident = MagicMock()
    incident.id = "eval-test-incident"
    incident.service_name = "payments-api"
    incident.severity = "P1"
    incident.title = "DB connection timeout — error rate 12.3%"
    incident.description = description
    incident.error_type = error_type
    incident.source = "prometheus"
    return incident


# ── Classifier quality evals ───────────────────────────────────────────────────

class TestClassifierQuality:

    async def test_db_incident_classified_as_db_issue(self):
        # A clear DB incident with "connection refused" and "pool" keywords
        # should reliably classify as db_issue, not unknown or network_issue.
        llm = make_real_llm()
        incident = make_incident(
            error_type="db_timeout",
            description="connection refused errors, DB pool exhausted, /charge endpoint down"
        )
        context = {"incident": incident}
        agent = ClassifierAgent(llm_svc=llm, context=context)
        context = await agent.run()

        assert context["incident_type"] == "db_issue", (
            f"Expected 'db_issue' for a clear DB incident, got '{context['incident_type']}'"
        )

    async def test_oom_incident_classified_as_memory_leak(self):
        # OOMKilled + heap pressure keywords should classify as memory_leak.
        llm = make_real_llm()
        incident = make_incident(
            error_type="oom",
            description="OOMKilled, heap pressure increasing, GC pauses > 5s, pod restarting"
        )
        context = {"incident": incident}
        agent = ClassifierAgent(llm_svc=llm, context=context)
        context = await agent.run()

        assert context["incident_type"] == "memory_leak", (
            f"Expected 'memory_leak' for OOM incident, got '{context['incident_type']}'"
        )

    async def test_classifier_never_returns_none(self):
        # For any input, the classifier must return a known category — never None.
        llm = make_real_llm()
        incident = make_incident(
            error_type="unknown",
            description="something weird is happening with the service"
        )
        context = {"incident": incident}
        agent = ClassifierAgent(llm_svc=llm, context=context)
        context = await agent.run()

        valid = {"db_issue", "memory_leak", "network_issue", "deploy_regression", "unknown"}
        assert context["incident_type"] in valid


# ── Hypothesis quality evals ───────────────────────────────────────────────────

class TestHypothesisQuality:

    async def test_high_confidence_for_clear_evidence(self):
        # When the evidence clearly points to a deploy changing pool_size,
        # HypothesisAgent should return confidence >= 0.8.
        llm = make_real_llm()
        incident = make_incident()
        context = {
            "incident": incident,
            "incident_type": "db_issue",
            "evidence_summary": (
                "100 total errors. Top patterns:\n"
                "100x: connection refused\n"
                "Deploy v2.4.1 at 2024-01-15 02:13 UTC by ci-bot: pool_size: 20→5\n"
                "Runbook: pool_size < 10 with traffic > 15 req/s causes connection exhaustion"
            )
        }
        agent = HypothesisAgent(llm_svc=llm, context=context)
        context = await agent.run()

        assert context["confidence"] >= 0.8, (
            f"Expected confidence >= 0.8 for clear evidence, got {context['confidence']}"
        )

    async def test_root_cause_mentions_pool_size(self):
        # The root cause for the canonical Sentinel scenario must reference
        # the pool_size config change — not a generic "database error" statement.
        llm = make_real_llm()
        incident = make_incident()
        context = {
            "incident": incident,
            "incident_type": "db_issue",
            "evidence_summary": (
                "847 connection refused errors in last 30 minutes.\n"
                "Deploy v2.4.1 changed pool_size from 20 to 5.\n"
                "Errors started 2 minutes after deploy."
            )
        }
        agent = HypothesisAgent(llm_svc=llm, context=context)
        context = await agent.run()

        root_cause_lower = context["root_cause"].lower()
        assert "pool" in root_cause_lower or "pool_size" in root_cause_lower, (
            f"Expected root cause to mention pool_size, got: {context['root_cause']}"
        )

    async def test_low_confidence_for_vague_evidence(self):
        # When the evidence is vague, confidence should be low (< 0.5).
        # The agent should NOT fabricate a high-confidence answer from nothing.
        llm = make_real_llm()
        incident = make_incident()
        context = {
            "incident": incident,
            "incident_type": "unknown",
            "evidence_summary": "Some errors were found. No deployments found. No runbook matches."
        }
        agent = HypothesisAgent(llm_svc=llm, context=context)
        context = await agent.run()

        assert context["confidence"] < 0.5, (
            f"Expected low confidence for vague evidence, got {context['confidence']}"
        )


# ── Responder quality evals ────────────────────────────────────────────────────

class TestResponderQuality:

    async def test_suggested_fix_contains_actionable_command(self):
        # The fix must include at least one actionable command — kubectl, git, or
        # a specific config change. "Check the database" is not acceptable.
        llm = make_real_llm()
        incident = make_incident()
        context = {
            "incident": incident,
            "incident_type": "db_issue",
            "root_cause": "pool_size reduced from 20 to 5 in v2.4.1 deploy",
            "confidence": 0.95,
            "reasoning": "Deploy timestamp matches error spike onset exactly",
            "evidence_summary": "100x connection refused, pool_size: 20→5 in v2.4.1"
        }
        agent = ResponderAgent(llm_svc=llm, context=context)
        context = await agent.run()

        fix = context["suggested_fix"].lower()
        # At least one of these actionable keywords must appear in the fix
        actionable_keywords = ["kubectl", "rollback", "rollout", "pool_size", "config", "revert"]
        has_actionable = any(kw in fix for kw in actionable_keywords)

        assert has_actionable, (
            f"Suggested fix is not actionable. Got: {context['suggested_fix'][:200]}"
        )

    async def test_escalation_for_low_confidence(self):
        # When confidence is 0.2, ResponderAgent must escalate.
        # This tests the hard rule end-to-end with a real LLM call.
        llm = make_real_llm()
        incident = make_incident()
        context = {
            "incident": incident,
            "incident_type": "unknown",
            "root_cause": "unclear cause — insufficient evidence",
            "confidence": 0.2,
            "reasoning": "no clear pattern found",
            "evidence_summary": "some errors, no deploys found"
        }
        agent = ResponderAgent(llm_svc=llm, context=context)
        context = await agent.run()

        # Hard rule must hold even with real LLM
        assert context["escalate"] is True, (
            "Expected escalate=True for confidence=0.2 but got False"
        )
