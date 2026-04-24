# =============================================================================
# FILE: backend/services/investigation_service.py
# WHAT: Orchestrates the full investigation pipeline for one incident.
#       This is the "brain" of Sentinel — it coordinates all services and
#       produces a root cause hypothesis saved to the DB.
# WHY:  Keeps the worker simple — it just calls InvestigationService.run().
#       All the orchestration logic lives here, not scattered across the worker.
# FLOW:
#   1. Fetch logs + deploys in parallel     (AlertService)
#   2. Summarise logs for cost control      (LogService.summarize)
#   3. Retrieve relevant runbook chunks     (RAGService)
#   4. Build a structured prompt            (_build_prompt)
#   5. Call Claude with all evidence        (LLMService)
#   6. Parse Claude's JSON response
#   7. Save Resolution to Postgres
#   8. Update Incident status to "resolved"
# OOP:  Composition — InvestigationService HAS-A AlertService, LLMService,
#       RAGService, IncidentRepository. It delegates to each one.
# CONNECTED TO:
#   ← services/alert_service.py          — parallel log + deploy gathering
#   ← services/log_service.py            — log summarisation
#   ← services/rag_service.py            — runbook retrieval
#   ← services/llm_service.py            — Claude API call + rule-based fallback
#   ← db/repositories/incident_repo.py   — DB reads/writes for incidents
#   ← db/models.py                       — Resolution ORM model
#   → workers/investigation_worker.py    — calls InvestigationService.run()
# =============================================================================

import json                                               # for parsing Claude's JSON response
from sqlalchemy.ext.asyncio import AsyncSession           # async DB session type
from db.models import Incident, Resolution                # ORM models for DB writes
from db.repositories.incident_repo import IncidentRepository  # incident DB operations
from services.alert_service import AlertService           # parallel log + deploy gathering
from services.log_service import LogService               # log summarisation
from services.llm_service import LLMService               # Claude API + fallback
from services.rag_service import RAGService               # runbook search


# System prompt — sent to Claude on every investigation.
# Defines Claude's role and enforces exact JSON output format.
# Strict format = reliable json.loads() parsing every time.
SYSTEM_PROMPT = """
You are an expert Site Reliability Engineer (SRE) investigating a production incident.
You will be given:
  - Alert details (service name, severity, error type)
  - Recent error logs (summarised)
  - Recent deployments (what changed and when)
  - Relevant runbook context (how similar issues were fixed before)

Your job: analyse all the evidence and identify the most likely root cause.

Respond ONLY with valid JSON in this exact format — no extra text, no markdown:
{
    "root_cause": "one clear sentence explaining what failed and why",
    "confidence": 0.87,
    "suggested_fix": "step-by-step fix written for an engineer woken at 3 AM",
    "evidence": [
        "evidence point 1 — quote specific numbers/values from the data",
        "evidence point 2"
    ]
}

Rules:
- confidence must be a float between 0.0 and 1.0
- evidence must reference actual data you were given — never fabricate
- suggested_fix must be actionable commands, not vague advice
"""


class InvestigationService:

    def __init__(self, db: AsyncSession, llm_svc: LLMService, rag_svc: RAGService):
        self.db = db
        self.llm_svc = llm_svc               # shared — holds circuit breaker state across calls
        self.rag_svc = rag_svc               # shared — model loaded once at startup, reused
        self.alert_svc = AlertService(db)    # coordinates log + deploy fetching
        self.log_svc = LogService(db)        # needed for .summarize()
        self.incident_repo = IncidentRepository(db)   # DB reads/writes for incidents

    async def run(self, incident: Incident) -> None:

        # ── Step 1: Gather logs + deploys in parallel ──────────────────────────
        # asyncio.gather inside AlertService runs both fetches concurrently.
        # Without this: fetch logs (300ms) THEN fetch deploys (300ms) = 600ms.
        # With this: both run at the same time = ~300ms total.
        logs, deploys = await self.alert_svc.gather_evidence(incident.service_name)

        # ── Step 2: Summarise logs ─────────────────────────────────────────────
        # Condenses up to 100 raw log lines into top 5 error patterns.
        # We send the SUMMARY to Claude — not the raw logs.
        # Reason: 100 log lines = ~2000 tokens. Summary = ~50 tokens. Big cost saving.
        log_summary = self.log_svc.summarize(logs)

        # ── Step 3: Retrieve runbook context via RAG ───────────────────────────
        # Searches FAISS + BM25 index built by scripts/build_index.py.
        # Uses alert description as query — returns top 5 relevant runbook chunks.
        # Example: "DB timeout connection refused" → finds the DB connection pool runbook.
        runbook_chunks = self.rag_svc.retrieve(incident.description)

        # ── Step 4: Build the prompt ───────────────────────────────────────────
        # Assembles all evidence into one structured string for Claude.
        # Structured sections (LOGS, DEPLOYS, RUNBOOKS) help Claude parse the context.
        prompt = self._build_prompt(incident, log_summary, deploys, runbook_chunks)

        # ── Step 5: Call Claude (with evidence for fallback) ───────────────────
        # Normal path: Claude reasons over evidence → returns JSON.
        # Fallback path (if Claude is down): LLMService runs rule-based Python logic
        #   on the same logs + deploys → returns best-effort JSON. No LLM used.
        raw_response = await self.llm_svc.call(
            prompt=prompt,
            system=SYSTEM_PROMPT,
            logs_summary=log_summary,       # fallback needs this to produce a real analysis
            deploys=deploys,                # fallback checks for deploy-error correlation
            runbook_chunks=runbook_chunks,  # fallback can reference runbook titles
        )

        # ── Step 6: Parse Claude's JSON response ──────────────────────────────
        # Claude was told to return only valid JSON, but sometimes wraps it in
        # markdown code fences like:
        #   ```json
        #   { ... }
        #   ```
        # json.loads() fails on the backticks, so we strip them first.
        cleaned = raw_response.strip()
        if cleaned.startswith("```"):
            # Drop the first line (```json or ```) and the last line (```)
            lines = cleaned.splitlines()
            cleaned = "\n".join(lines[1:-1]).strip()

        try:
            result = json.loads(cleaned)        # parse the JSON string into a Python dict
        except json.JSONDecodeError:
            print(f"[InvestigationService] JSON parse failed. Raw: {raw_response[:200]}")
            result = {
                "root_cause": "Unable to parse LLM response. Manual review required.",
                "confidence": 0.0,
                "suggested_fix": "Review raw logs and deploys manually.",
                "evidence": [],
            }

        # ── Step 7: Save Resolution to Postgres ───────────────────────────────
        # Resolution = the AI's output for this incident.
        # One row per incident (enforced by unique constraint on incident_id).
        # evidence is stored as JSONB — a list of strings saved as real JSON in Postgres.
        resolution = Resolution(
            incident_id=incident.id,
            root_cause=result.get("root_cause", "Unknown"),
            confidence=float(result.get("confidence", 0.0)),   # ensure float, not string
            suggested_fix=result.get("suggested_fix", "No fix suggested."),
            evidence=result.get("evidence", []),                # JSONB list
            llm_model_used=self.llm_svc.model,                  # e.g. "claude-haiku-4-5-20251001"
        )
        self.db.add(resolution)     # stage the INSERT — not written to DB yet

        # ── Step 8: Update Incident status to "resolved" ──────────────────────
        # Tells the dashboard the investigation is complete.
        # Even a low-confidence result is "resolved" — the confidence score signals
        # how much the engineer should trust it.
        await self.incident_repo.update_status(incident.id, "resolved")

        # Commit BOTH the resolution insert and the status update atomically.
        # If anything fails, BOTH are rolled back — DB stays consistent.
        await self.db.commit()

        print(
            f"[InvestigationService] Incident {incident.id} resolved. "
            f"Root cause: {result.get('root_cause', '')[:80]}... "
            f"Confidence: {result.get('confidence', 0.0):.0%}"
        )

    def _build_prompt(
        self,
        incident: Incident,
        log_summary: str,
        deploys: list,
        runbook_chunks: list,
    ) -> str:
        # Format deploy list into readable lines.
        # Each line shows: version, timestamp, who deployed, what changed.
        if deploys:
            deploy_text = "\n".join([
                f"  - {d.version} deployed at {d.deployed_at} "
                f"by {d.deployed_by}: {d.diff_summary}"
                for d in deploys
            ])
        else:
            deploy_text = "  No deployments found in the last 2 hours."

        # Join runbook chunks with a separator so Claude can distinguish between them.
        if runbook_chunks:
            runbook_text = "\n---\n".join(runbook_chunks)
        else:
            runbook_text = "  No relevant runbook context found."

        # The full prompt Claude receives.
        # Structured sections make it easy for Claude to find the relevant data.
        return f"""
INCIDENT DETAILS:
  Service:     {incident.service_name}
  Severity:    {incident.severity}
  Title:       {incident.title}
  Description: {incident.description}
  Error type:  {incident.error_type}
  Source:      {incident.source}

RECENT ERROR LOGS (last 30 minutes):
{log_summary}

RECENT DEPLOYMENTS (last 2 hours):
{deploy_text}

RELEVANT RUNBOOK CONTEXT:
{runbook_text}

Analyse all the above evidence and return your JSON investigation result.
"""
