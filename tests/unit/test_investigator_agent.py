# =============================================================================
# FILE: tests/unit/test_investigator_agent.py
# WHAT: Unit tests for InvestigatorAgent — the ReAct loop agent.
# WHY:  InvestigatorAgent is the most complex piece — it has a loop, parses
#       LLM responses, calls tools, and builds conversation history.
#       If _parse_action() breaks, every tool call silently fails.
#       If the DONE check breaks, the agent loops forever.
#       We test these behaviours in isolation from the real LLM and real DB.
# WHAT WE TEST:
#   1. _parse_action() correctly extracts ACTION and INPUT from LLM text
#   2. _parse_action() returns ("", {}) on malformed responses
#   3. Calling an unknown tool returns an error observation (doesn't crash)
#   4. When LLM says DONE, the loop stops and evidence_summary is set
#   5. When a tool is called, its result appears as an observation in history
# =============================================================================

import pytest
from unittest.mock import AsyncMock, MagicMock

from agents.investigator_agent import InvestigatorAgent
from tools.base_tool import BaseTool


# ── Fake tool for testing ──────────────────────────────────────────────────────

class FakeTool(BaseTool):
    """
    A minimal tool implementation for tests — returns a fixed string.
    We use a concrete subclass (not MagicMock) because InvestigatorAgent
    accesses tool.name and tool.description as properties, which MagicMock
    handles poorly with @property decorators.
    """

    def __init__(self, name: str, result: str = "tool result"):
        self._name = name
        self._result = result

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"A fake {self._name} tool for testing"

    async def run(self, input_data: dict) -> str:
        return self._result


# ── Helpers ────────────────────────────────────────────────────────────────────

def make_agent(llm_responses: list[str], mock_incident, tools=None):
    """
    Create an InvestigatorAgent whose LLM returns responses from the list
    in order — first call returns responses[0], second returns responses[1], etc.
    """
    llm_svc = MagicMock()
    # side_effect with a list makes AsyncMock return each item in turn
    llm_svc.call_with_messages = AsyncMock(side_effect=llm_responses)
    llm_svc.model = "claude-haiku-test"

    context = {
        "incident": mock_incident,
        "incident_type": "db_issue",
    }

    if tools is None:
        tools = [FakeTool("fetch_logs"), FakeTool("fetch_deploys")]

    return InvestigatorAgent(llm_svc=llm_svc, context=context, tools=tools)


# ── _parse_action() tests ──────────────────────────────────────────────────────

class TestParseAction:

    def setup_method(self):
        # We need an agent instance to call _parse_action() — use any valid setup
        self.agent = make_agent(["DONE: done"], MagicMock())

    def test_parses_valid_action_and_input(self):
        response = (
            "THOUGHT: I should check the logs\n"
            "ACTION: fetch_logs\n"
            'INPUT: {"service_name": "payments-api"}'
        )
        action, input_data = self.agent._parse_action(response)
        assert action == "fetch_logs"
        assert input_data == {"service_name": "payments-api"}

    def test_parses_action_with_extra_whitespace(self):
        response = (
            "THOUGHT: checking\n"
            "ACTION:   fetch_deploys  \n"
            'INPUT: {"service_name": "api"}'
        )
        action, _ = self.agent._parse_action(response)
        assert action == "fetch_deploys"

    def test_returns_empty_on_missing_action(self):
        # Response has no ACTION line — agent should return ("", {}) not crash
        response = "THOUGHT: I am thinking but not acting"
        action, input_data = self.agent._parse_action(response)
        assert action == ""
        assert input_data == {}

    def test_returns_empty_on_invalid_json_input(self):
        # Malformed JSON in INPUT — should fail gracefully, not raise
        response = (
            "THOUGHT: ok\n"
            "ACTION: fetch_logs\n"
            "INPUT: {not valid json}"
        )
        action, input_data = self.agent._parse_action(response)
        assert action == ""
        assert input_data == {}

    def test_returns_empty_on_missing_input(self):
        # ACTION present but no INPUT line
        response = "ACTION: fetch_logs"
        action, input_data = self.agent._parse_action(response)
        # action parses fine, input defaults to {}
        assert action == "fetch_logs"
        assert input_data == {}


# ── _call_tool() tests ─────────────────────────────────────────────────────────

class TestCallTool:

    async def test_calls_correct_tool_by_name(self, mock_incident):
        tool = FakeTool("fetch_logs", result="847 connection refused errors")
        agent = make_agent(["DONE: done"], mock_incident, tools=[tool])

        result = await agent._call_tool("fetch_logs", {"service_name": "payments-api"})
        assert "847 connection refused errors" in result

    async def test_unknown_tool_returns_error_not_crash(self, mock_incident):
        # If the LLM hallucinates a tool name that doesn't exist,
        # the agent should return an error observation — not raise an exception.
        agent = make_agent(["DONE: done"], mock_incident)

        result = await agent._call_tool("nonexistent_tool", {})
        assert "unknown tool" in result.lower()
        assert "nonexistent_tool" in result


# ── Full ReAct loop tests ──────────────────────────────────────────────────────

class TestReActLoop:

    async def test_done_exits_loop_immediately(self, mock_incident):
        # When the LLM says DONE on the first iteration, the loop stops.
        # evidence_summary should be set from the DONE text.
        agent = make_agent(
            ["THOUGHT: I have enough info\nDONE: Pool size was reduced from 20 to 5"],
            mock_incident
        )
        context = await agent.run()

        assert "Pool size was reduced" in context["evidence_summary"]
        # LLM should only have been called once (one iteration)
        assert agent.llm_svc.call_with_messages.call_count == 1

    async def test_tool_called_then_done(self, mock_incident):
        # First iteration: call a tool. Second: say DONE.
        # Verifies the loop runs multiple times correctly.
        tool = FakeTool("fetch_logs", result="100 errors found")
        agent = make_agent(
            [
                "THOUGHT: check logs\nACTION: fetch_logs\nINPUT: {\"service_name\": \"payments-api\"}",
                "THOUGHT: enough info\nDONE: Found 100 connection refused errors",
            ],
            mock_incident,
            tools=[tool]
        )
        context = await agent.run()

        assert "100 connection refused errors" in context["evidence_summary"]
        assert agent.llm_svc.call_with_messages.call_count == 2

    async def test_observation_added_to_history(self, mock_incident):
        # After a tool runs, its result should appear in the conversation
        # history as an OBSERVATION message so the LLM sees it next iteration.
        tool = FakeTool("fetch_logs", result="TOOL RESULT HERE")
        agent = make_agent(
            [
                'THOUGHT: ok\nACTION: fetch_logs\nINPUT: {"service_name": "x"}',
                "DONE: done",
            ],
            mock_incident,
            tools=[tool]
        )
        await agent.run()

        # Get the messages list passed to the second LLM call
        second_call_args = agent.llm_svc.call_with_messages.call_args_list[1]
        messages_passed = second_call_args[1]["messages"]   # keyword arg

        # The tool result should appear in the messages
        observation_messages = [
            m for m in messages_passed
            if "OBSERVATION" in m.get("content", "")
        ]
        assert len(observation_messages) > 0
        assert "TOOL RESULT HERE" in observation_messages[0]["content"]

    async def test_max_iterations_stops_loop(self, mock_incident):
        # If the LLM never says DONE, the loop must stop at MAX_ITERATIONS.
        # We return a non-DONE response for every call.
        from agents.investigator_agent import MAX_ITERATIONS

        # Always return a tool call, never DONE — agent should hit the limit
        always_call_tool = (
            'THOUGHT: keep going\nACTION: fetch_logs\nINPUT: {"service_name": "x"}'
        )
        tool = FakeTool("fetch_logs", result="some logs")
        agent = make_agent(
            [always_call_tool] * (MAX_ITERATIONS + 5),   # more than enough responses
            mock_incident,
            tools=[tool]
        )
        context = await agent.run()

        # Loop must have stopped — call count should be MAX_ITERATIONS
        assert agent.llm_svc.call_with_messages.call_count == MAX_ITERATIONS
        # Evidence summary must still be set (fallback synthesis)
        assert context["evidence_summary"] != ""
