# =============================================================================
# FILE: backend/agents/base_agent.py
# WHAT: Abstract base class that all Sentinel agents inherit from.
#       Defines the shared interface and common behaviour every agent needs.
# WHY:  Without a base class, each agent would re-implement LLM calling,
#       context access, and response cleaning independently — messy and error-prone.
#       The base class enforces a contract: every agent MUST have a system_prompt
#       and a run() method. The shared think() method means LLM call logic
#       lives in exactly one place.
# DESIGN PATTERNS:
#   Template Method — BaseAgent defines the skeleton (think → run).
#                     Subclasses fill in system_prompt() and run().
#   Blackboard      — `context` dict is the shared memory between agents.
#                     ClassifierAgent writes `incident_type`,
#                     InvestigatorAgent writes `evidence_summary`,
#                     HypothesisAgent writes `root_cause` + `confidence`,
#                     ResponderAgent writes `suggested_fix` + `escalate`.
#                     Each agent reads what the previous one wrote.
# OOP CONCEPTS:
#   Abstraction    — ABC hides the "how" (LLM call details) from subclasses.
#   Inheritance    — all agents inherit think() and context access for free.
#   Encapsulation  — LLM call and response cleaning are private to this class.
# CONNECTED TO:
#   ← services/llm_service.py   — think() delegates LLM calls here
#   → agents/classifier_agent.py   — inherits from BaseAgent
#   → agents/investigator_agent.py — inherits from BaseAgent
#   → agents/hypothesis_agent.py   — inherits from BaseAgent
#   → agents/responder_agent.py    — inherits from BaseAgent
# =============================================================================

from abc import ABC, abstractmethod          # ABC = Abstract Base Class; abstractmethod marks methods
                                             # that MUST be implemented by every subclass
from services.llm_service import LLMService  # all LLM calls go through this one service


class BaseAgent(ABC):
    # ABC = Abstract Base Class. You CANNOT instantiate BaseAgent directly.
    # It exists only to be inherited. If you try: BaseAgent(...) → TypeError.
    # This forces every agent to implement system_prompt() and run().

    def __init__(self, llm_svc: LLMService, context: dict):
        # llm_svc: shared LLMService instance — holds circuit breaker state.
        #   Passed in (not created here) so all agents share the SAME circuit breaker.
        #   If ClassifierAgent causes 2 failures and InvestigatorAgent causes 1 more,
        #   the circuit opens correctly. If each agent created its own LLMService,
        #   failure counts would reset per agent and the circuit would never open.
        self.llm_svc = llm_svc

        # context: the "blackboard" — a shared dict all agents read from and write to.
        #   Starts empty, gets filled as agents run in sequence:
        #     {"incident": ..., "incident_type": ..., "evidence_summary": ...,
        #      "root_cause": ..., "confidence": ..., "suggested_fix": ..., "escalate": ...}
        #   Passing the same dict to all agents (by reference) means writes from one
        #   agent are immediately visible to the next. No copying needed.
        self.context = context

    @abstractmethod
    def system_prompt(self) -> str:
        # Every agent must define its own system prompt.
        # The system prompt tells Claude what role it's playing and what format to return.
        # @abstractmethod means: if a subclass doesn't implement this, Python raises
        # TypeError at class definition time — fail fast, not at runtime.
        ...

    @abstractmethod
    async def run(self) -> dict:
        # Every agent must implement run() — this is where the agent's logic lives.
        # run() reads from self.context, does its job, writes results back to self.context,
        # and returns the updated context so the orchestrator can pass it to the next agent.
        # async because most agents make at least one LLM call (async I/O).
        ...

    async def think(self, user_message: str) -> str:
        # Single-turn LLM call: system prompt + one user message → one response.
        # Used by ClassifierAgent, HypothesisAgent, ResponderAgent — agents that
        # send one well-structured prompt and get one structured response back.
        #
        # This is a CONCRETE method (not abstract) — all agents inherit it for free.
        # None of them need to know HOW the LLM is called (which model, which API,
        # how the circuit breaker works). That's all hidden in LLMService.
        #
        # Template Method pattern: think() is the "template" — the base defines it,
        # subclasses call it without knowing the implementation details.
        response = await self.llm_svc.call(
            prompt=user_message,
            system=self.system_prompt(),    # each agent's own role definition
        )
        return self._strip_markdown(response)

    async def think_with_history(self, messages: list[dict]) -> str:
        # Multi-turn LLM call: system prompt + full conversation history → next response.
        # Used ONLY by InvestigatorAgent for its ReAct loop.
        #
        # In the ReAct loop:
        #   - messages grows with each iteration: [user, assistant, user, assistant, ...]
        #   - Each "user" message after the first is a tool observation
        #   - Each "assistant" message is the agent's THOUGHT + ACTION
        # The LLM sees the full history each time, so it knows what it already tried.
        #
        # `messages` format: [{"role": "user", "content": "..."}, {"role": "assistant", ...}]
        response = await self.llm_svc.call_with_messages(
            messages=messages,
            system=self.system_prompt(),
        )
        return self._strip_markdown(response)

    def _strip_markdown(self, text: str) -> str:
        # Claude sometimes wraps its response in markdown code fences:
        #   ```json
        #   { ... }
        #   ```
        # json.loads() fails on the backticks. Strip them before returning.
        # This is a private helper — the underscore prefix signals "internal use only".
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            cleaned = "\n".join(lines[1:-1]).strip()
        return cleaned
