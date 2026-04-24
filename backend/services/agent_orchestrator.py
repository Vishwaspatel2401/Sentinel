# =============================================================================
# FILE: backend/services/agent_orchestrator.py
# WHAT: Runs all 4 agents in sequence for one incident investigation.
#       Replaces InvestigationService for the Week 2 agent pipeline.
# WHY:  The worker (investigation_worker.py) only needs to call one thing.
#       AgentOrchestrator hides the complexity of wiring agents together,
#       passing the shared context, creating tools, and saving the result.
#       If you add a 5th agent later, you change only this file.
# FLOW:
#   1. Build shared context dict (the "blackboard")
#   2. Run ClassifierAgent  → writes "incident_type"
#   3. Run InvestigatorAgent → writes "evidence_summary"
#   4. Run HypothesisAgent  → writes "root_cause", "confidence"
#   5. Run ResponderAgent   → writes "suggested_fix", "escalate", "evidence"
#   6. Save Resolution to Postgres + update incident status → "resolved"
# DESIGN PATTERNS:
#   Blackboard   — shared context dict passed to every agent; each one reads
#                  what the previous agent wrote and adds its own output.
#   Facade       — hides all agent + tool wiring behind one simple run() method.
#                  The worker just calls: await orchestrator.run(incident)
#   Composition  — HAS-A LLMService, RAGService, DB session. Delegates to each.
# OOP:
#   Encapsulation — all agent/tool wiring is hidden inside this class.
#                   Nothing outside knows which agents run or in what order.
# CONNECTED TO:
#   ← db/models.py                   — Resolution ORM model for saving results
#   ← db/repositories/incident_repo.py — update_status() after investigation
#   ← services/llm_service.py         — shared LLM instance (circuit breaker state)
#   ← services/rag_service.py         — shared RAG instance (model loaded once)
#   ← services/log_service.py         — used by FetchLogsTool
#   ← services/deploy_service.py      — used by FetchDeploysTool
#   ← agents/classifier_agent.py      — first agent
#   ← agents/investigator_agent.py    — second agent (ReAct loop)
#   ← agents/hypothesis_agent.py      — third agent
#   ← agents/responder_agent.py       — fourth agent
#   ← tools/fetch_logs_tool.py        — tool given to InvestigatorAgent
#   ← tools/fetch_deploys_tool.py     — tool given to InvestigatorAgent
#   ← tools/runbook_tool.py           — tool given to InvestigatorAgent
#   → workers/investigation_worker.py — calls AgentOrchestrator.run()
# =============================================================================

import logging                                           # structured logging
from sqlalchemy.ext.asyncio import AsyncSession         # async DB session type

from db.models import Incident, Resolution              # ORM models
from db.repositories.incident_repo import IncidentRepository  # DB writes

from services.llm_service import LLMService             # shared LLM + circuit breaker
from services.rag_service import RAGService             # shared embedding model
from services.log_service import LogService             # used by FetchLogsTool
from services.deploy_service import DeployService       # used by FetchDeploysTool

from agents.classifier_agent import ClassifierAgent     # agent 1
from agents.investigator_agent import InvestigatorAgent # agent 2
from agents.hypothesis_agent import HypothesisAgent     # agent 3
from agents.responder_agent import ResponderAgent       # agent 4

from tools.fetch_logs_tool import FetchLogsTool         # tool 1
from tools.fetch_deploys_tool import FetchDeploysTool   # tool 2
from tools.runbook_tool import RunbookTool              # tool 3

logger = logging.getLogger(__name__)


class AgentOrchestrator:

    def __init__(self, db: AsyncSession, llm_svc: LLMService, rag_svc: RAGService):
        self.db = db
        self.llm_svc = llm_svc          # shared — circuit breaker state persists across agents
        self.rag_svc = rag_svc          # shared — embedding model loaded once at startup
        self.incident_repo = IncidentRepository(db)

    async def run(self, incident: Incident) -> None:

        logger.info("Starting agent pipeline", extra={"incident_id": str(incident.id)})

        # ── Build the shared context (blackboard) ─────────────────────────────
        # All 4 agents read from and write to this single dict.
        # It starts with just the incident — each agent adds its own output.
        # Using a dict (not a class) keeps it flexible: agents can add any key.
        context = {
            "incident": incident,        # the full Incident ORM object
            "incident_type": None,       # set by ClassifierAgent
            "evidence_summary": "",      # set by InvestigatorAgent
            "root_cause": "",            # set by HypothesisAgent
            "confidence": 0.0,           # set by HypothesisAgent
            "reasoning": "",             # set by HypothesisAgent
            "suggested_fix": "",         # set by ResponderAgent
            "escalate": False,           # set by ResponderAgent
            "escalation_reason": "",     # set by ResponderAgent
            "evidence": [],              # set by ResponderAgent
        }

        # ── Build tools for InvestigatorAgent ─────────────────────────────────
        # Tools need DB services — create them with the current session.
        # All three tools are passed to InvestigatorAgent; it picks which to call.
        log_svc    = LogService(self.db)
        deploy_svc = DeployService(self.db)

        tools = [
            FetchLogsTool(log_svc),          # lets agent call: ACTION: fetch_logs
            FetchDeploysTool(deploy_svc),     # lets agent call: ACTION: fetch_deploys
            RunbookTool(self.rag_svc),        # lets agent call: ACTION: search_runbooks
        ]

        # ── Agent 1: ClassifierAgent ───────────────────────────────────────────
        # Reads incident, classifies type, writes context["incident_type"]
        logger.info("Running ClassifierAgent", extra={"incident_id": str(incident.id)})
        classifier = ClassifierAgent(self.llm_svc, context)
        context = await classifier.run()

        # ── Agent 2: InvestigatorAgent ─────────────────────────────────────────
        # ReAct loop — calls tools, gathers evidence, writes context["evidence_summary"]
        logger.info("Running InvestigatorAgent", extra={"incident_id": str(incident.id)})
        investigator = InvestigatorAgent(self.llm_svc, context, tools)
        context = await investigator.run()

        # ── Agent 3: HypothesisAgent ───────────────────────────────────────────
        # Reads evidence, forms root cause + confidence, writes to context
        logger.info("Running HypothesisAgent", extra={"incident_id": str(incident.id)})
        hypothesis = HypothesisAgent(self.llm_svc, context)
        context = await hypothesis.run()

        # ── Agent 4: ResponderAgent ────────────────────────────────────────────
        # Reads hypothesis, writes fix + escalation decision to context
        logger.info("Running ResponderAgent", extra={"incident_id": str(incident.id)})
        responder = ResponderAgent(self.llm_svc, context)
        context = await responder.run()

        # ── Save results to Postgres ───────────────────────────────────────────
        # All 4 agents have run. context now contains the full investigation result.
        # Save it as a Resolution row — same table as Week 1.
        # This means the GET /incidents/{id} endpoint works without changes.
        resolution = Resolution(
            incident_id=incident.id,
            root_cause=context["root_cause"],
            confidence=context["confidence"],
            suggested_fix=context["suggested_fix"],
            evidence=context["evidence"],            # list of strings → JSONB
            llm_model_used=self.llm_svc.model,
        )
        self.db.add(resolution)

        # Update the incident status from "investigating" → "resolved"
        await self.incident_repo.update_status(incident.id, "resolved")

        # Commit both the resolution INSERT and the status UPDATE atomically.
        # If either fails, neither is written — DB stays consistent.
        await self.db.commit()

        logger.info(
            "Incident resolved",
            extra={
                "incident_id":   str(incident.id),
                "incident_type": context["incident_type"],
                "confidence":    round(context["confidence"], 2),
                "escalate":      context["escalate"],
            }
        )
