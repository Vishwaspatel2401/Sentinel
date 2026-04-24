# =============================================================================
# FILE: backend/services/llm_service.py
# WHAT: The single class that talks to the Anthropic (Claude) API.
#       Nobody else in the codebase imports `anthropic` directly — they all
#       go through this class.
# WHY:  Centralising LLM calls means:
#         1. Circuit breaker protects the whole app if Claude goes down
#         2. If you swap Claude for GPT-4, you change ONE file
#         3. You can add logging, token counting, retries in one place
# PATTERN: Circuit Breaker — if Claude fails 3 times in a row, the circuit
#          "opens" and all calls return a fallback instantly instead of
#          waiting 30s for a timeout. After 60s, it tries again (half-open).
# FALLBACK: When Claude is unavailable, instead of returning "LLM unavailable"
#           the system runs rule-based code analysis on the already-gathered
#           evidence (logs + deploys) to produce a best-effort analysis.
#           NO LLM is used in the fallback — it is pure Python if/else logic.
# CONNECTED TO:
#   ← config.py provides anthropic_api_key and llm_model
#   → services/investigation_service.py calls llm_svc.call() to reason
#   → agents/base_agent.py (Week 2) calls llm_svc.call() for each agent step
# =============================================================================

import json                          # for building the fallback JSON response
import logging                       # structured logging
import time                          # for tracking when the circuit breaker should reset
import anthropic                     # Anthropic's official Python SDK
from config import settings          # api key + model name

logger = logging.getLogger(__name__)


class LLMService:

    # How many consecutive failures before the circuit opens
    MAX_FAILURES = 3

    # How many seconds to wait before trying again after circuit opens
    RESET_TIMEOUT = 60

    def __init__(self):
        # Anthropic client — authenticated with your API key from .env
        # This is created once and reused for every call (efficient)
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

        # Which Claude model to use — set in .env as LLM_MODEL
        self.model = settings.llm_model

        # --- Circuit breaker state ---
        self.failure_count = 0       # how many consecutive failures have happened
        self.circuit_open = False    # True = stop calling Claude, return fallback immediately
        self.opened_at: float = 0.0  # timestamp of when the circuit opened (for reset timer)

    async def call(
        self,
        prompt: str,
        system: str,
        # Optional evidence passed in so the fallback can still produce a real analysis.
        # These are already gathered BEFORE the LLM call — if Claude fails, we use them.
        logs_summary: str = "",          # summarised log patterns e.g. "847x: connection refused"
        deploys: list = [],              # list of Deploy ORM objects fetched by DeployService
        runbook_chunks: list = [],       # list of relevant runbook strings from RAGService
    ) -> str:

        # --- Check circuit breaker BEFORE calling Claude ---
        if self.circuit_open:
            time_since_open = time.time() - self.opened_at
            if time_since_open < self.RESET_TIMEOUT:
                # Circuit still open — skip Claude, run rule-based analysis immediately
                logger.warning("Circuit open — running rule-based fallback", extra={"retry_in_seconds": round(self.RESET_TIMEOUT - time_since_open)})
                return self._fallback_response(logs_summary, deploys, runbook_chunks)
            else:
                # Enough time passed — try one call (half-open state)
                logger.info("Circuit half-open — attempting one call")
                self.circuit_open = False

        try:
            # --- Call the Anthropic API ---
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1024,         # cap response length — prevents runaway costs
                temperature=0.1,         # low = deterministic structured JSON output
                system=system,           # tells Claude its role + required output format
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )

            # Success — reset failure counter
            self.failure_count = 0

            # response.content is a list — [0] is the first (and only) content block
            return response.content[0].text

        except anthropic.APIStatusError as e:
            return self._handle_failure(
                f"API error {e.status_code}: {e.message}",
                logs_summary, deploys, runbook_chunks
            )

        except anthropic.APIConnectionError:
            return self._handle_failure(
                "Connection error — couldn't reach Anthropic API",
                logs_summary, deploys, runbook_chunks
            )

        except Exception as e:
            return self._handle_failure(
                f"Unexpected error: {str(e)}",
                logs_summary, deploys, runbook_chunks
            )

    async def call_with_messages(self, messages: list[dict], system: str) -> str:
        # Multi-turn version of call() — used by InvestigatorAgent's ReAct loop.
        # `messages` is a list of {"role": "user"/"assistant", "content": "..."} dicts
        # that represent the full conversation so far.
        # Each ReAct iteration appends the agent's response and the tool observation
        # to this list, so the LLM always sees the full context.
        #
        # This uses the same circuit breaker check as call() — if Claude is down,
        # return a minimal fallback string so the loop can exit gracefully.
        if self.circuit_open:
            time_since_open = time.time() - self.opened_at
            if time_since_open < self.RESET_TIMEOUT:
                logger.warning("Circuit open — skipping multi-turn call")
                return "DONE: Circuit breaker open — unable to investigate further."
            else:
                self.circuit_open = False

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                temperature=0.1,
                system=system,
                messages=messages    # full conversation history — not just one message
            )
            self.failure_count = 0
            return response.content[0].text

        except Exception as e:
            self.failure_count += 1
            logger.error("Multi-turn LLM call failed", extra={"error": str(e), "failure_count": self.failure_count})
            if self.failure_count >= self.MAX_FAILURES:
                self.circuit_open = True
                self.opened_at = time.time()
            return "DONE: LLM error — unable to investigate further."

    def _handle_failure(
        self, reason: str,
        logs_summary: str, deploys: list, runbook_chunks: list
    ) -> str:
        # Increment failure counter and open circuit if threshold reached
        self.failure_count += 1
        logger.error(
            "LLM call failed",
            extra={"failure_count": self.failure_count, "max": self.MAX_FAILURES, "reason": reason}
        )

        if self.failure_count >= self.MAX_FAILURES:
            self.circuit_open = True
            self.opened_at = time.time()
            logger.critical("Circuit OPENED", extra={"retry_in_seconds": self.RESET_TIMEOUT})

        # Run rule-based analysis on the evidence we already have
        return self._fallback_response(logs_summary, deploys, runbook_chunks)

    def _fallback_response(
        self,
        logs_summary: str,
        deploys: list,
        runbook_chunks: list,
    ) -> str:
        # =====================================================================
        # RULE-BASED ANALYSIS — no LLM involved, pure Python if/else logic.
        # Analyses already-gathered evidence to produce a best-effort result.
        # confidence is always low (0.1-0.2) to signal this is not AI reasoning.
        # fallback_used=True lets the dashboard show a warning banner.
        # =====================================================================

        evidence = []   # collect evidence strings to include in the response

        # Add log summary to evidence if we have it
        if logs_summary and logs_summary != "No recent errors found.":
            evidence.append(f"Logs: {logs_summary}")

        # Add deploy info to evidence if we have it
        for deploy in deploys:
            evidence.append(
                f"Deploy {deploy.version} at {deploy.deployed_at} "
                f"by {deploy.deployed_by}: {deploy.diff_summary}"
            )

        # --- Rule 1: Deploy happened close to when errors started ---
        # Most common cause of production incidents is a bad deploy.
        # If we have both deploys AND errors, correlate them.
        if deploys and logs_summary and logs_summary != "No recent errors found.":
            latest = deploys[0]  # already ordered most-recent-first by DeployService
            return json.dumps({
                "root_cause": (
                    f"Recent deployment {latest.version} at {latest.deployed_at} "
                    f"may have caused this incident. "
                    f"Change: {latest.diff_summary}"
                ),
                "confidence": 0.2,   # low — rule-based correlation, not causal reasoning
                "suggested_fix": (
                    f"1. Review deploy {latest.version} by {latest.deployed_by}.\n"
                    f"2. Consider rollback: kubectl rollout undo deployment/<service>\n"
                    f"3. LLM unavailable — verify this analysis manually."
                ),
                "evidence": evidence,
                "escalate": True,        # always escalate — human should verify rule-based output
                "fallback_used": True    # dashboard uses this to show a warning banner
            })

        # --- Rule 2: High volume of errors, no recent deploy ---
        # Suggests infrastructure issue rather than a code change.
        if logs_summary and logs_summary != "No recent errors found.":
            return json.dumps({
                "root_cause": (
                    f"High error volume detected with no recent deployment. "
                    f"Likely infrastructure or dependency issue. Errors: {logs_summary}"
                ),
                "confidence": 0.15,
                "suggested_fix": (
                    "1. Check downstream dependencies (DB, cache, external APIs).\n"
                    "2. Check infrastructure health (CPU, memory, disk).\n"
                    "3. LLM unavailable — manual investigation required."
                ),
                "evidence": evidence,
                "escalate": True,
                "fallback_used": True
            })

        # --- Rule 3: Deploy found but no error logs yet ---
        # Errors may not have accumulated yet — flag the deploy as suspicious.
        if deploys:
            latest = deploys[0]
            return json.dumps({
                "root_cause": (
                    f"Recent deployment {latest.version} found "
                    f"but no error logs yet. Monitor closely."
                ),
                "confidence": 0.1,
                "suggested_fix": (
                    f"Monitor error rate after deploy {latest.version}. "
                    f"Roll back if errors spike."
                ),
                "evidence": evidence,
                "escalate": True,
                "fallback_used": True
            })

        # --- Rule 4: No evidence at all ---
        # Nothing to work with — tell the engineer to investigate manually.
        return json.dumps({
            "root_cause": "No logs or deploys found. Unable to determine cause.",
            "confidence": 0.0,
            "suggested_fix": "Check service health manually. LLM unavailable.",
            "evidence": [],
            "escalate": True,
            "fallback_used": True
        })
