# =============================================================================
# FILE: backend/agents/investigator_agent.py
# WHAT: The core investigation agent. Uses a ReAct loop to gather evidence
#       by autonomously deciding which tools to call and in what order.
# WHY:  Week 1's InvestigationService always fetched logs + deploys + runbooks,
#       in the same order, no matter what the incident was.
#       InvestigatorAgent REASONS about what to do next:
#         "I see connection refused errors → let me check if a deploy changed pool_size"
#         "The deploy changed pool_size → let me search runbooks for pool exhaustion"
#       This is the difference between a script and an agent.
# PATTERN: ReAct = Reason + Act
#   Each iteration:
#     1. THOUGHT — Claude reasons about what it knows and what to do next
#     2. ACTION  — Claude picks a tool to call (or says DONE)
#     3. INPUT   — Claude provides the tool's input as JSON
#     [agent calls the tool → gets observation]
#     4. OBSERVATION added to conversation history
#     [repeat from step 1 with full history]
#   Loop ends when Claude says DONE or MAX_ITERATIONS is reached.
# DESIGN PATTERNS:
#   Template Method  — implements system_prompt() and run() from BaseAgent.
#   Strategy         — tools are interchangeable strategies for evidence gathering.
#   Polymorphism     — agent calls tool.run() on any BaseTool subclass without
#                      knowing which concrete type it is.
# OOP:
#   Composition      — HAS-A list of tools. Delegates data gathering to them.
#   Inheritance      — inherits think_with_history() from BaseAgent.
# CONNECTED TO:
#   ← agents/base_agent.py         — inherits BaseAgent
#   ← tools/fetch_logs_tool.py     — one of the tools in self.tools
#   ← tools/fetch_deploys_tool.py  — one of the tools in self.tools
#   ← tools/runbook_tool.py        — one of the tools in self.tools
#   → services/agent_orchestrator.py — creates this agent with tools injected
#   → agents/hypothesis_agent.py   — reads "evidence_summary" from context
# =============================================================================

import json                              # for parsing the LLM's ACTION INPUT JSON
import logging
from agents.base_agent import BaseAgent  # abstract base — provides think_with_history()
from services.llm_service import LLMService
from tools.base_tool import BaseTool     # type hint for the tools list

logger = logging.getLogger(__name__)


# Maximum number of ReAct iterations before forcing the loop to stop.
# Prevents infinite loops if the LLM keeps calling tools without concluding.
# 5 iterations = enough for: fetch_logs → fetch_deploys → search_runbooks → done
MAX_ITERATIONS = 5


class InvestigatorAgent(BaseAgent):

    def __init__(self, llm_svc: LLMService, context: dict, tools: list[BaseTool]):
        super().__init__(llm_svc, context)

        # tools: list of BaseTool objects the agent can call.
        # Injected from outside (Dependency Injection) so the orchestrator controls
        # which tools are available. You could disable a tool by not passing it here.
        self.tools = tools

        # Build a lookup dict: {"fetch_logs": <FetchLogsTool>, ...}
        # Used in _call_tool() to find the right tool by name in O(1).
        self.tool_map = {tool.name: tool for tool in tools}

    def system_prompt(self) -> str:
        # The system prompt does two things:
        #   1. Tells Claude what role it plays
        #   2. Lists all available tools with their descriptions so Claude
        #      knows what it can call and what input each tool expects
        #
        # The tool descriptions are generated dynamically from each tool's
        # .description property — adding a new tool auto-updates this prompt.
        tool_descriptions = "\n".join([
            f"  - {tool.name}: {tool.description}"
            for tool in self.tools
        ])

        return f"""
You are a senior SRE investigating a production incident. You have access to tools
that let you gather real evidence from the system.

Available tools:
{tool_descriptions}

You MUST follow this exact format on every response:

  THOUGHT: [your reasoning about what to investigate next]
  ACTION: [tool_name — must be one of the available tool names above]
  INPUT: [JSON input for the tool, e.g. {{"service_name": "payments-api"}}]

When you have gathered enough evidence to explain the incident, respond with:

  THOUGHT: [summary of what you found]
  DONE: [a concise summary of all evidence gathered — include specific numbers, versions, timestamps]

Rules:
  - You MUST call at least 2 tools before saying DONE.
  - Always use EXACT tool names from the list above.
  - INPUT must be valid JSON on a single line.
  - DONE summary must reference actual data from tool results, never fabricate.
"""

    async def run(self) -> dict:
        incident = self.context["incident"]
        incident_type = self.context.get("incident_type", "unknown")

        # Build the first user message — gives the agent the incident context
        # and the classification from the previous agent to guide its strategy.
        initial_message = f"""
Investigate this production incident:

  Service:        {incident.service_name}
  Severity:       {incident.severity}
  Title:          {incident.title}
  Description:    {incident.description}
  Error type:     {incident.error_type}
  Classification: {incident_type}

Use your tools to gather evidence. Start with the most likely cause based on the classification.
"""

        # messages: the growing conversation history for the multi-turn ReAct loop.
        # Starts with one user message. Each iteration adds:
        #   {"role": "assistant", "content": "<THOUGHT + ACTION + INPUT>"}
        #   {"role": "user",      "content": "OBSERVATION: <tool result>"}
        messages = [{"role": "user", "content": initial_message}]

        evidence_summary = ""    # will be set when the agent says DONE

        for iteration in range(MAX_ITERATIONS):
            logger.info("ReAct iteration", extra={"iteration": iteration + 1, "max": MAX_ITERATIONS})

            # Call Claude with the full conversation history so far.
            # BaseAgent.think_with_history() sends messages + system_prompt to LLMService.
            response = await self.think_with_history(messages)

            # Add Claude's response to history as "assistant" turn.
            # Important: this must happen BEFORE we check for DONE or call a tool,
            # so the next LLM call sees the full unmodified history.
            messages.append({"role": "assistant", "content": response})

            # ── Check if the agent is done ──────────────────────────────────────
            if "DONE:" in response:
                # Extract the summary after "DONE:" — this is the evidence digest
                # that HypothesisAgent will read.
                done_idx = response.index("DONE:")
                evidence_summary = response[done_idx + len("DONE:"):].strip()
                logger.info("Investigation done", extra={"iterations": iteration + 1})
                break

            # ── Parse ACTION and INPUT ──────────────────────────────────────────
            action_name, tool_input = self._parse_action(response)

            if not action_name:
                # LLM didn't follow the format — add a nudge and retry
                messages.append({
                    "role": "user",
                    "content": "OBSERVATION: Invalid format. You must include ACTION: and INPUT: or say DONE:."
                })
                continue

            # ── Call the tool ───────────────────────────────────────────────────
            observation = await self._call_tool(action_name, tool_input)
            logger.info("Tool returned observation", extra={"tool": action_name, "preview": observation[:100]})

            # Add the tool result as the next "user" message.
            # The LLM sees: "OBSERVATION: [what the tool returned]"
            # This is the "Observe" part of Reason → Act → Observe.
            messages.append({
                "role": "user",
                "content": f"OBSERVATION: {observation}"
            })

        else:
            # Loop finished without the agent saying DONE — hit MAX_ITERATIONS.
            # Synthesize what we have from the conversation history.
            logger.warning("Max iterations reached — synthesising from history", extra={"max": MAX_ITERATIONS})
            evidence_summary = self._synthesize_from_history(messages)

        # Write the evidence summary to the blackboard.
        # HypothesisAgent reads "evidence_summary" to form its root cause hypothesis.
        self.context["evidence_summary"] = evidence_summary

        # Also store the full conversation for debugging / audit trail
        self.context["investigation_messages"] = messages

        return self.context

    def _parse_action(self, response: str) -> tuple[str, dict]:
        # Parse the LLM's response to extract ACTION name and INPUT dict.
        # Expected format:
        #   THOUGHT: some reasoning
        #   ACTION: fetch_logs
        #   INPUT: {"service_name": "payments-api"}
        #
        # Returns (action_name, input_dict) or ("", {}) if parsing fails.
        try:
            action_name = ""
            tool_input = {}

            for line in response.splitlines():
                line = line.strip()
                if line.startswith("ACTION:"):
                    # Extract the tool name — everything after "ACTION: "
                    action_name = line[len("ACTION:"):].strip()
                elif line.startswith("INPUT:"):
                    # Extract the JSON input — everything after "INPUT: "
                    raw_input = line[len("INPUT:"):].strip()
                    tool_input = json.loads(raw_input)   # parse JSON → dict

            return action_name, tool_input

        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("Failed to parse action from LLM response", extra={"error": str(e)})
            return "", {}

    async def _call_tool(self, action_name: str, tool_input: dict) -> str:
        # Look up the tool by name in the pre-built dict.
        # If the LLM hallucinated a tool name that doesn't exist, return an error
        # observation — the LLM will see it and try a different tool.
        tool = self.tool_map.get(action_name)
        if not tool:
            available = ", ".join(self.tool_map.keys())
            return f"Error: unknown tool '{action_name}'. Available tools: {available}"

        # Call the tool — returns a string observation.
        # All tool.run() calls are async (DB queries), so we await them.
        return await tool.run(tool_input)

    def _synthesize_from_history(self, messages: list[dict]) -> str:
        # Fallback: hit max iterations without DONE.
        # Collect all OBSERVATION lines from the conversation to build a summary.
        observations = []
        for msg in messages:
            if msg["role"] == "user" and msg["content"].startswith("OBSERVATION:"):
                observations.append(msg["content"])

        if observations:
            return "Evidence gathered (max iterations reached):\n" + "\n".join(observations)
        return "No evidence gathered — investigation did not complete."
