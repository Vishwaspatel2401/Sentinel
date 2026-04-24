# =============================================================================
# FILE: tests/unit/test_llm_service.py
# WHAT: Unit tests for LLMService — specifically the circuit breaker logic
#       and the rule-based fallback (no real Anthropic API calls made).
# WHY:  LLMService has two important behaviours that must never break:
#       1. Circuit breaker: opens after 3 failures, stops hammering a dead API
#       2. Fallback: returns useful analysis even when Claude is down
#       If the circuit breaker breaks → app hammers the API until it's banned.
#       If the fallback breaks → incidents show "unknown" when Claude is down.
# KEY CONCEPT — Mocking:
#   We don't call the real Anthropic API in tests — that costs money and is slow.
#   Instead, we patch() the client to either succeed or raise an exception.
#   patch() replaces the real object for the duration of the test, then restores it.
# =============================================================================

import json
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
import anthropic

from services.llm_service import LLMService


# ── Helpers ────────────────────────────────────────────────────────────────────

def make_service() -> LLMService:
    """Create a fresh LLMService with a mocked Anthropic client."""
    with patch("services.llm_service.anthropic.Anthropic") as mock_anthropic:
        # MagicMock() replaces the real Anthropic client so no HTTP calls happen
        mock_anthropic.return_value = MagicMock()
        svc = LLMService()
    return svc


def make_deploy(version="v2.4.1", diff="pool_size: 20→5") -> MagicMock:
    d = MagicMock()
    d.version = version
    d.deployed_at = "2024-01-15 02:13"
    d.deployed_by = "ci-bot"
    d.diff_summary = diff
    return d


# ── Circuit breaker tests ──────────────────────────────────────────────────────

class TestCircuitBreaker:

    def test_circuit_starts_closed(self):
        # A brand-new LLMService should have the circuit closed (not open).
        # circuit_open = False means we'll try to call Claude normally.
        svc = make_service()
        assert svc.circuit_open is False
        assert svc.failure_count == 0

    async def test_circuit_opens_after_max_failures(self):
        # After MAX_FAILURES (3) consecutive failures, the circuit should open.
        # Once open, subsequent calls return the fallback instantly.
        svc = make_service()

        # Simulate 3 API failures by making the client raise an exception
        svc.client.messages.create.side_effect = Exception("API down")

        # Call 3 times — each call should fail and increment failure_count
        for _ in range(3):
            await svc.call(prompt="test", system="test")

        assert svc.circuit_open is True
        assert svc.failure_count >= 3

    async def test_failure_count_resets_on_success(self):
        # If Claude recovers after 2 failures, the failure count resets to 0.
        # A 3rd failure should NOT open the circuit (fresh count).
        svc = make_service()

        # Make the Anthropic response object look real
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"ok": true}')]

        # Fail twice, then succeed
        svc.client.messages.create.side_effect = [
            Exception("fail 1"),
            Exception("fail 2"),
            mock_response,   # 3rd call succeeds
        ]

        await svc.call(prompt="t", system="t")   # fail 1
        await svc.call(prompt="t", system="t")   # fail 2
        await svc.call(prompt="t", system="t")   # success

        # After a success, failure_count must be 0
        assert svc.failure_count == 0
        assert svc.circuit_open is False

    async def test_open_circuit_skips_api_call(self):
        # When the circuit is open, LLMService must NOT call the Anthropic API.
        # It should return the fallback immediately without making any HTTP call.
        svc = make_service()
        svc.circuit_open = True
        svc.opened_at = __import__("time").time()  # just opened — not yet expired

        await svc.call(prompt="test", system="test")

        # The Anthropic client should NOT have been called at all
        svc.client.messages.create.assert_not_called()


# ── Fallback rule tests ────────────────────────────────────────────────────────

class TestFallbackResponse:
    """
    Tests for the 4 rule-based fallback rules in _fallback_response().
    Each rule produces a different result based on what evidence is available.
    """

    def test_deploy_plus_logs_returns_deploy_correlation(self):
        # Rule 1: we have BOTH a deploy and error logs.
        # The fallback should correlate them and suggest the deploy as the cause.
        svc = make_service()
        deploy = make_deploy()
        logs_summary = "100x: connection refused"

        result_str = svc._fallback_response(logs_summary, [deploy], [])
        result = json.loads(result_str)

        assert "v2.4.1" in result["root_cause"]           # references the deploy version
        assert result["confidence"] == 0.2                 # low — rule-based, not causal
        assert result["fallback_used"] is True
        assert result["escalate"] is True

    def test_logs_no_deploys_returns_infra_issue(self):
        # Rule 2: we have error logs but NO recent deploy.
        # The fallback should suggest an infrastructure issue.
        svc = make_service()
        logs_summary = "50x: memory allocation failed"

        result_str = svc._fallback_response(logs_summary, [], [])
        result = json.loads(result_str)

        assert "infrastructure" in result["root_cause"].lower()
        assert result["confidence"] == 0.15
        assert result["fallback_used"] is True

    def test_deploy_no_logs_returns_monitor_message(self):
        # Rule 3: we have a deploy but no errors yet.
        # The fallback should flag the deploy and say "monitor closely".
        svc = make_service()
        deploy = make_deploy()

        result_str = svc._fallback_response("No recent errors found.", [deploy], [])
        result = json.loads(result_str)

        assert result["confidence"] == 0.1
        assert result["fallback_used"] is True

    def test_no_evidence_returns_zero_confidence(self):
        # Rule 4: nothing at all. Fallback should say it can't determine the cause.
        svc = make_service()

        result_str = svc._fallback_response("No recent errors found.", [], [])
        result = json.loads(result_str)

        assert result["confidence"] == 0.0
        assert result["evidence"] == []
        assert result["fallback_used"] is True

    def test_fallback_response_is_valid_json(self):
        # All fallback paths must return a valid JSON string.
        # If json.loads() fails, InvestigationService crashes.
        svc = make_service()
        deploy = make_deploy()

        for logs, deploys in [
            ("100x: error", [deploy]),
            ("100x: error", []),
            ("No recent errors found.", [deploy]),
            ("No recent errors found.", []),
        ]:
            result_str = svc._fallback_response(logs, deploys, [])
            # This should not raise — if it does, the test fails with a clear error
            parsed = json.loads(result_str)
            assert "root_cause" in parsed
            assert "confidence" in parsed
            assert "suggested_fix" in parsed
