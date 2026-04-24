# =============================================================================
# FILE: backend/tools/fetch_deploys_tool.py
# WHAT: Tool that fetches recent deployments for a service.
#       Wraps DeployService so InvestigatorAgent can call it as a "tool action".
# WHY:  Same reason as FetchLogsTool — agents should not import services directly.
#       The tool layer keeps the agent generic and swappable.
# OOP:  Composition — FetchDeploysTool HAS-A DeployService.
#       Inheritance — implements the BaseTool interface.
# CONNECTED TO:
#   ← tools/base_tool.py           — inherits BaseTool interface
#   ← services/deploy_service.py   — delegates fetch here
#   → agents/investigator_agent.py — added to the agent's tools list
# =============================================================================

from tools.base_tool import BaseTool                 # abstract interface all tools implement
from services.deploy_service import DeployService    # does the actual DB query


class FetchDeploysTool(BaseTool):

    def __init__(self, deploy_svc: DeployService):
        self.deploy_svc = deploy_svc

    @property
    def name(self) -> str:
        return "fetch_deploys"

    @property
    def description(self) -> str:
        return (
            "Fetches recent deployments for a service from the last 2 hours. "
            "Input: {\"service_name\": \"<name>\"}. "
            "Returns version, timestamp, who deployed, and what changed (diff_summary)."
        )

    async def run(self, input_data: dict) -> str:
        service_name = input_data.get("service_name", "")

        if not service_name:
            return "Error: service_name is required. Provide {\"service_name\": \"<name>\"}."

        # Fetch deployments from DB — ordered most-recent-first by DeployService
        deploys = await self.deploy_svc.fetch_recent_deploys(service_name)

        if not deploys:
            return f"[fetch_deploys result for '{service_name}']\nNo deployments found in the last 2 hours."

        # Format each deploy as a readable line the LLM can reason over.
        # e.g. "v2.4.1 deployed at 2024-01-15 02:13 by ci-bot: pool_size: 20→5"
        lines = [
            f"  - {d.version} at {d.deployed_at.strftime('%Y-%m-%d %H:%M')} UTC "
            f"by {d.deployed_by}: {d.diff_summary}"
            for d in deploys
        ]

        return f"[fetch_deploys result for '{service_name}']\n" + "\n".join(lines)
