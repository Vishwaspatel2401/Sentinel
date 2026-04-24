# =============================================================================
# FILE: backend/tools/base_tool.py
# WHAT: Abstract base class for all tools the InvestigatorAgent can call.
#       Every tool must have a name, a description, and a run() method.
# WHY:  InvestigatorAgent holds a list of tools and loops over them to find
#       the right one to call. To do that safely, every tool needs the same
#       interface. Without a base class, the agent would have to know the
#       specific type of each tool — brittle and hard to extend.
# DESIGN PATTERNS:
#   Strategy      — each tool is a different strategy for gathering evidence.
#                   The agent picks which strategy to use at runtime.
#   Polymorphism  — the agent calls tool.run(input) on ANY tool without knowing
#                   which concrete class it is. All tools respond to the same call.
# OOP CONCEPTS:
#   Abstraction   — BaseTool hides the implementation behind a common interface.
#   Inheritance   — FetchLogsTool, FetchDeploysTool, RunbookTool all inherit name/description.
# CONNECTED TO:
#   → tools/fetch_logs_tool.py    — inherits from BaseTool
#   → tools/fetch_deploys_tool.py — inherits from BaseTool
#   → tools/runbook_tool.py       — inherits from BaseTool
#   ← agents/investigator_agent.py — holds a list[BaseTool] and calls tool.run()
# =============================================================================

from abc import ABC, abstractmethod   # ABC makes BaseTool un-instantiable directly


class BaseTool(ABC):

    @property
    @abstractmethod
    def name(self) -> str:
        # The tool's identifier — used by InvestigatorAgent to find the right tool.
        # e.g. "fetch_logs", "fetch_deploys", "search_runbooks"
        # The LLM outputs "ACTION: fetch_logs" and the agent matches it to this name.
        # @property means it's accessed as tool.name (not tool.name())
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        # Human-readable description included in the system prompt sent to the LLM.
        # The LLM reads this to decide WHEN to use each tool.
        # e.g. "Fetches recent ERROR logs for a given service from the last 30 minutes."
        ...

    @abstractmethod
    async def run(self, input_data: dict) -> str:
        # Execute the tool with the given input and return a string observation.
        # InvestigatorAgent passes the LLM's parsed INPUT as a dict,
        # e.g. {"service_name": "payments-api"} for fetch_logs.
        # Returns a plain string — the "observation" the LLM reads on the next iteration.
        # async because FetchLogsTool and FetchDeploysTool query the database.
        ...
