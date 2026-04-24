
### MVP Build Guide · Multi-Agent Edition · Resume-Impact Focus · 3-Week Plan

> **The goal**: Build the smallest system that proves depth. A tight, believable, end-to-end system beats a half-built "enterprise" project every time. **What this teaches**: LLD, HLD, distributed systems thinking, database design, RAG, agentic AI — from fundamentals to industry patterns. **What this costs**: $0–$20 total. 3 focused weeks.

---

## 1. The Problem You're Solving

### 🟢 Simple

A production alert fires at 3 AM. An engineer gets woken up. They now have to:

- Manually search logs across multiple services
- Check recent deployment history across 3 different dashboards
- Read runbooks scattered across Confluence, GitHub wikis, and Notion
- Form a diagnosis while the system is on fire

**This takes 30+ minutes on average and burns out good engineers.**

Existing tools (PagerDuty, OpsGenie) only _notify_. They don't investigate anything. No tool currently looks at logs + deploys + runbooks together and gives you a hypothesis automatically.

**Sentinel fills that gap — and goes further. It doesn't just correlate data, it thinks about what data to gather, adapts its investigation strategy based on what it finds, and hands off a complete, engineer-ready answer.**

### 🔵 Advanced

Production incident response suffers from a fundamental **context aggregation problem**: the information required to diagnose a failure is distributed across heterogeneous, siloed systems — structured log stores, deployment pipelines, and unstructured runbook repositories — with no automated correlation layer.

Existing observability tools (PagerDuty, OpsGenie, Datadog) operate exclusively at the **detection and notification layer**. They identify anomalies and route alerts, but provide zero investigative automation. The mean time to diagnosis (MTTD) remains engineer-dependent, averaging 20–45 minutes for P1 incidents.

A naive RAG pipeline partially solves this — it pre-fetches context and sends it to an LLM in one shot. But a fixed pipeline is **blind**: it can't say "these logs aren't relevant, let me search differently" or "I'm not confident, let me gather more evidence." The investigation path is identical regardless of what the alert actually is.

**Sentinel implements a multi-agent investigation system**: a Classifier agent triages the alert and determines which evidence sources are relevant; an Investigator agent runs a ReAct loop — reasoning about what tools to call, calling them, observing results, deciding whether to continue; a Hypothesis agent synthesizes raw evidence into a structured causal chain; a Responder agent produces engineer-ready remediation steps. All agents share a persistent `AgentMemory` object providing a full audit trail. This reduces MTTD from 30+ minutes to under 10 seconds while adapting the investigation strategy to the specific incident type.

---

## 2. The Solution — Core Loop

### 🟢 Simple

```
Alert (JSON) → Agent Pipeline → Root Cause Hypothesis → Suggested Fix
```

Four things Sentinel must do:

|What|How|Why It Matters|
|---|---|---|
|**Classify** alert|Classifier Agent decides what kind of incident this is|Don't waste time on irrelevant tools|
|**Investigate** adaptively|Investigator Agent runs a loop: think → call tool → observe → repeat|Gathers only what's needed, stops when confident|
|**Hypothesize**|Hypothesis Agent reads all evidence and forms root cause|Clean analytical reasoning separate from data gathering|
|**Respond**|Responder Agent writes engineer-ready fix|3 AM language, not analyst language|

### 🔵 Advanced

```
Alert (JSON) → Classifier → Investigator (ReAct loop) → Hypothesis → Responder → Result
```

|What|How|Why It Matters|
|---|---|---|
|**Classify**|Fast LLM call: incident_type + relevant tools + skip tools|Prevents irrelevant tool calls; DB timeout shouldn't check K8s pods|
|**Investigate**|ReAct loop: THOUGHT → ACTION (tool call) → OBSERVATION → repeat ≤6 steps|Adaptive evidence gathering; stops when `confidence > 0.85` or max steps hit|
|**Hypothesize**|Receives clean evidence bundle, no raw tool outputs|Isolated reasoning context → better causal chain quality|
|**Respond**|Receives hypothesis, produces sequential remediation|Terse, actionable, written for someone woken at 3 AM|

The key insight: each agent has **one job and a smaller context window**. A single LLM doing everything — tool routing, evidence reading, causal reasoning, and response writing — produces lower quality than four specialized agents with focused prompts and clean inputs.

---

## 3. Architecture

### 3.1 High-Level Diagram

### 🟢 Simple

```
┌──────────────────────────────────────────────────┐
│                   CLIENT / CURL                  │
│        POST /alerts     GET /incidents/{id}      │
└──────────────────┬───────────────────────────────┘
                   │
┌──────────────────▼───────────────────────────────┐
│            FastAPI — API Gateway                 │
│   Auth (API key)  ·  Validation  ·  Rate limit   │
└──────────────────┬───────────────────────────────┘
                   │  Enqueue to Redis
┌──────────────────▼───────────────────────────────┐
│              Redis Queue                         │
│   alert:queue  (list)  ·  dedup key (set)        │
└──────────────────┬───────────────────────────────┘
                   │  Background worker picks up
┌──────────────────▼───────────────────────────────┐
│           AgentOrchestrator (NEW)                │
│                                                  │
│  ┌──────────────────────────────────────────┐   │
│  │  1. ClassifierAgent                      │   │
│  │     "What kind of incident? What tools?" │   │
│  └──────────────┬───────────────────────────┘   │
│                 │                                │
│  ┌──────────────▼───────────────────────────┐   │
│  │  2. InvestigatorAgent  (ReAct loop)      │   │
│  │     Think → Call Tool → Observe → Repeat │   │
│  │                                          │   │
│  │  Tools: LogTool · DeployTool ·           │   │
│  │         RunbookTool · MetricsTool        │   │
│  └──────────────┬───────────────────────────┘   │
│                 │                                │
│  ┌──────────────▼───────────────────────────┐   │
│  │  3. HypothesisAgent                      │   │
│  │     Evidence → Causal chain + confidence │   │
│  └──────────────┬───────────────────────────┘   │
│                 │                                │
│  ┌──────────────▼───────────────────────────┐   │
│  │  4. ResponderAgent                       │   │
│  │     Hypothesis → Engineer-ready fix      │   │
│  └──────────────┬───────────────────────────┘   │
│                 │                                │
│  Shared: AgentMemory (full audit trail)          │
└──────────────────┬───────────────────────────────┘
                   │
┌──────────────────▼───────────────────────────────┐
│                PostgreSQL                        │
│  incidents · resolutions · agent_traces          │
└──────────────────────────────────────────────────┘
                   │
┌──────────────────▼───────────────────────────────┐
│             Next.js Dashboard                    │
│  Incident list · Agent trace panel · AI output   │
└──────────────────────────────────────────────────┘
```

### 🔵 Advanced

The system replaces the monolithic `InvestigationService` with an `AgentOrchestrator` that sequences four specialized agents, each writing to a shared `AgentMemory` object. This is the **Blackboard architectural pattern** — agents communicate through a shared knowledge store rather than direct calls, enabling each agent to read prior agents' outputs without tight coupling. The memory object is persisted in Postgres as a JSONB column, providing a complete audit trail of every reasoning step for debugging and evaluation.

### 3.2 Agent Pipeline Detail

#### 🟢 Simple

```
Alert arrives
  ↓
[Classifier Agent] — fast, cheap
  → "This is a DB incident. Use: logs, deploys, runbooks. Skip: k8s pods."

  ↓
[Investigator Agent] — adaptive loop
  Step 1: THOUGHT: "DB incident. Start with logs."
          ACTION: search_logs("payments-api", "30min")
          OBSERVATION: "847 connection refused errors"

  Step 2: THOUGHT: "Connection errors spiked. Check deploys."
          ACTION: search_deploys("payments-api", hours=2)
          OBSERVATION: "Deploy at 01:27 AM changed pool_size: 20→5"

  Step 3: THOUGHT: "Strong signal. Verify with runbook."
          ACTION: search_runbooks("connection pool exhaustion")
          OBSERVATION: "pool_size < concurrent_requests causes timeout cascade"

  Step 4: THOUGHT: "Confidence > 0.85. Stop."
          ACTION: finish(evidence=[...])

  ↓
[Hypothesis Agent] — pure reasoning, no tool calls
  → root_cause, confidence, causal_chain, alternative_hypotheses

  ↓
[Responder Agent] — engineer-ready output
  → summary, immediate_actions, root_fix, escalate: false
```

#### 🔵 Advanced

```
Alert
  ↓
ClassifierAgent (1 LLM call, ~500ms)
  → incident_type: "database"
  → suggested_tools: ["search_logs", "search_deploys", "search_runbooks"]
  → skip_tools: ["check_kubernetes_pods"]
  → priority: "high"

  ↓
InvestigatorAgent (ReAct loop, max 6 steps)
  Each step: LLM receives [system_prompt + memory.tool_history] →
             outputs {thought, action, action_input} →
             ToolRegistry.execute(action, action_input) →
             appends observation to memory →
             LLM decides: continue or finish

  Termination: action == "finish" OR step_count == MAX_STEPS (6)
  On MAX_STEPS: sets investigator_confidence = 0.0, escalate = True

  ↓
HypothesisAgent (1 LLM call)
  Input: memory.evidence (clean list, not raw tool outputs)
  Output: {root_cause, confidence, causal_chain[], alternative_hypotheses[]}
  Key design: receives summarized evidence, NOT raw log lines →
              smaller context, better analytical reasoning

  ↓
ResponderAgent (1 LLM call)
  Input: memory.hypothesis
  Output: {summary, immediate_actions[], root_fix, escalate, notify[]}
  Key design: writes for an engineer woken at 3 AM —
              terse, sequential, commands over explanations
```

Total LLM calls per investigation: Classifier(1) + Investigator(2–6) + Hypothesis(1) + Responder(1) = **5–9 calls**. Cost at GPT-4o-mini rates: ~$0.03–0.10 per investigation.

### 3.3 RAG Pipeline (unchanged, now called by RunbookTool)

#### 🟢 Simple

RAG = Retrieval Augmented Generation. Before asking the AI a question, first find the most relevant parts of your runbooks and give them to the AI as context. In the agentic version, the Investigator Agent calls `search_runbooks()` as a **tool** — it decides when to call it and what query to use, rather than always fetching it at the start.

**One-time setup:**

```
Your runbook .md files
    → Split into chunks of ~512 words
    → Turn each chunk into a number (embedding)
    → Save in a searchable index (FAISS + BM25)
```

**Called by Investigator Agent when needed:**

```
Agent decides: "I need runbook context for this error type"
    → RunbookTool.run(query="connection pool exhaustion DB timeout")
    → Search FAISS (similar meaning) + BM25 (exact keywords)
    → Return top 5 chunks as observation
```

#### 🔵 Advanced

```
OFFLINE (one-time setup — unchanged):
Runbook .md files → [Text chunker] → 512-word chunks, 128-word overlap
    → [Embedder] sentence-transformers/all-MiniLM-L6-v2
    → [FAISS IndexFlatIP] saved to disk
    → [BM25Okapi] saved as pkl

ONLINE (called by RunbookTool during agent ReAct loop):
agent_query → [Dense search FAISS top-k=10] + [BM25 top-k=10]
    → [Normalize + merge: 0.6 dense / 0.4 sparse]
    → [Service name boost ×1.2]
    → top 5 chunks → returned as tool OBSERVATION
```

The key architectural difference from the pipeline version: RAG is no longer called unconditionally at investigation start. The Investigator Agent calls `search_runbooks()` only when it decides runbook context is relevant — and with a **query it composes based on what it has already observed**, not the raw alert description. This produces more targeted retrieval.

### 3.4 Data Flow (Write Path vs Read Path)

#### 🟢 Simple

```
WRITE PATH: Alert → Redis Queue → Worker → AgentOrchestrator → PostgreSQL
READ PATH:  Browser → FastAPI → PostgreSQL → JSON response
```

Same separation as before. Writing is slow (agents take 5–15 seconds total), reading is fast (<10ms).

#### 🔵 Advanced

Same CQRS pattern applies. The write path now has longer latency (5–15s vs 3–5s) due to multiple sequential LLM calls, but this is still asynchronous and non-blocking. The `AgentMemory` object is written to Redis incrementally (after each agent step) for recovery in case of worker crash, and to Postgres atomically on completion. The read path is unchanged.

---

## 4. Tech Stack

### 🟢 Simple

|What it does|Tool|Why this one|
|---|---|---|
|API|FastAPI|Async-native, auto-generates docs, built-in validation|
|Queue|Redis|Simple, local, teaches you producer/consumer pattern|
|Database|PostgreSQL|Industry standard. Same DB used at Uber and Stripe|
|ORM|SQLAlchemy + asyncpg|Standard way to talk to Postgres from Python|
|Migrations|Alembic|How you version your database schema in production|
|Vector search|FAISS|Free, local, teaches you how embeddings and search work|
|Keyword search|rank-bm25|Adds hybrid retrieval at zero extra cost|
|Embeddings|sentence-transformers|Free local model, no API cost|
|LLM|GPT-4o-mini ($5–20 total)|Multiple agent calls — API quality matters here|
|**Agent framework**|**Custom ReAct loop (hand-rolled)**|**You understand every line. No LangChain black box.**|
|Frontend|Next.js|Visual impact, you already know it|
|Containers|Docker + Compose|Makes the whole stack run on any machine|
|Config|Pydantic Settings|Type-safe environment variables|

**Skip for now**: Kafka, Kubernetes, LangChain, LangGraph, AutoGen. Build your own ReAct loop — you'll understand it deeply and be able to explain it.

### 🔵 Advanced

|Layer|Tool|Architectural rationale|
|---|---|---|
|**API**|FastAPI|ASGI-native; Pydantic v2 validation at request/response boundaries|
|**Queue**|Redis (list as queue)|`LPUSH`/`BLPOP` for at-most-once delivery; `SETNX` for distributed locking|
|**Database**|PostgreSQL|ACID guarantees; JSONB for `AgentMemory` storage and agent traces|
|**ORM**|SQLAlchemy 2.0 + asyncpg|`AsyncSession` with asyncpg bypasses GIL for I/O-bound DB operations|
|**Migrations**|Alembic|Versioned, reversible schema migrations|
|**Vector search**|FAISS `IndexFlatIP`|Exact nearest-neighbor on normalized vectors; in-process, zero latency|
|**Keyword search**|rank-bm25 (BM25Okapi)|Compensates for dense search weakness on exact token matching|
|**Embeddings**|sentence-transformers `all-MiniLM-L6-v2`|384-dim; 80MB; CPU-native in ~50ms per batch|
|**LLM**|GPT-4o-mini (tiered per agent)|`response_format: json_object` for all agent outputs; `temperature: 0.1`; model tiering routes Classifier/Investigator/Responder to cheaper calls, HypothesisAgent to a stronger model if needed|
|**Agent framework**|**Hand-rolled ReAct loop**|No abstraction overhead; full control over tool dispatch, observation formatting, termination logic; interviewers can ask about every line|
|**Agent communication**|`AgentMemory` (Pydantic model)|Blackboard pattern — agents write to shared state, no direct coupling|
|**Frontend**|Next.js App Router|5s polling; agent trace panel shows step-by-step reasoning|
|**Containers**|Docker Compose|Service isolation with health checks|

**Why not LangChain/LangGraph?** These frameworks abstract away the ReAct loop, tool dispatch, and memory management. That abstraction is valuable in production but harmful for a portfolio project — you can't explain what you didn't build. A hand-rolled ReAct loop is 150 lines of Python you can defend completely in an interview.

---

## 5. What You Will Actually Learn

### 5.1 Core Coding Fundamentals

#### 🟢 Simple

**This is the most underrated payoff.** If you treat each piece as a real engineering problem — not just "make it work" — you will develop instincts that most junior engineers never get.

**A. Stop writing "God files"** — The agent system takes this further. Not only does each service have one job, each _agent_ has one job. The Investigator doesn't write the response. The Responder doesn't call tools.

**B. Async programming** — Agent steps must be sequential (each step depends on the previous observation), but tool calls within a step can be parallel. You'll learn when to use `asyncio.gather` and when you _can't_.

**C. Debugging agentic flows** — Harder than debugging a pipeline. The agent might call unexpected tools, get stuck in a loop, or produce low-confidence output for non-obvious reasons. You'll learn to log every thought, action, and observation, and read agent traces like a stack trace.

**D. Prompt engineering at depth** — Four agents means four specialized system prompts. Each prompt must enforce output format, constrain scope, and handle edge cases. You'll learn what makes a prompt reliable vs fragile.

**E. Hallucination is worse in agents than in single LLM calls** — In a one-shot pipeline, a hallucination gives you a wrong answer. In a ReAct loop, a hallucination in step 2 pollutes the evidence for step 3, which corrupts the hypothesis, which produces a wrong fix. One bad step cascades. You'll learn to build defenses at every agent boundary: grounding checks, schema validation, confidence calibration.

#### 🔵 Advanced

**A. Agent Orchestration as a Design Problem**

The `AgentOrchestrator` implements sequential agent chaining with shared state — each agent reads prior agents' outputs from `AgentMemory` and appends its own. This is the **Blackboard pattern**: agents are decoupled from each other (Hypothesis Agent doesn't import InvestigatorAgent), but they share a common knowledge store. The alternative — passing outputs as direct function arguments — creates tight coupling and makes it impossible to add agents without changing signatures throughout the chain.

**B. ReAct Loop Mechanics**

The ReAct (Reason + Act) pattern implements a controlled agentic loop: LLM outputs `{thought, action, action_input}` → `ToolRegistry` dispatches to the correct tool → tool returns observation → observation is appended to the LLM's message history → LLM decides next action. The critical implementation detail: **the LLM's context window accumulates the full thought-action-observation history**, giving it memory of what it has tried. Without this accumulation, the agent would repeat the same tool call indefinitely.

**C. Prompt Engineering for Reliability**

Each agent's system prompt must do four things: (1) define the agent's role and scope precisely, (2) specify exact JSON output schema with field-level descriptions, (3) enumerate failure modes and how to handle them, (4) include a termination condition. A missing termination condition is the most common cause of agent loops. `MAX_STEPS = 6` is the hard termination guard that no prompt can override.

**D. Hallucination Compounds Across Agent Steps**

In a single-shot LLM call, a hallucination produces a wrong answer. In a ReAct loop, hallucination is structurally worse: a fabricated evidence point in step 2 enters `memory.evidence`, which the HypothesisAgent reasons over, producing a confident-sounding but entirely wrong root cause, which the ResponderAgent converts into a wrong remediation command that an on-call engineer might actually execute at 3 AM. This is why Sentinel applies hallucination defenses at **every agent output boundary** — not just at the final output. The four failure modes are: (1) Classifier assigns wrong incident type → Investigator gets wrong tools, (2) Investigator calls a non-existent tool name, (3) Investigator fabricates evidence in `finish` that it never actually observed, (4) HypothesisAgent constructs a causal chain ungrounded in any evidence. Each requires a different defense.

---

### 5.2 Low-Level Design (LLD)

#### 🟢 Simple

LLD = how you design the _inside_ of your codebase: classes, interfaces, patterns.

**New patterns the agent system adds:**

|Pattern|Where it shows up|What it teaches|
|---|---|---|
|**Strategy**|`BaseTool` → `LogTool`, `DeployTool`, `RunbookTool`|Swap tools without changing the agent|
|**Template Method**|`BaseAgent.run()` calls abstract `_build_prompt()` and `_parse_output()`|Common agent lifecycle with custom steps per agent|
|**Blackboard**|`AgentMemory` shared by all agents|Agents communicate through state, not direct calls|
|**Command**|`ToolCall(name, input, output)` logged in memory|Each tool invocation is a replayable record|
|**Repository**|`IncidentRepository` handles all DB queries|Keep database code separate from business logic|
|**Circuit Breaker**|LLM fails → fallback|Handle failures instead of crashing|

**BaseAgent interface:**

```python
class BaseAgent(ABC):
    @abstractmethod
    def _build_prompt(self, memory: AgentMemory) -> str:
        """Each agent defines its own prompt from memory state."""
        pass

    @abstractmethod
    def _parse_output(self, raw: dict) -> Any:
        """Each agent defines how to parse the LLM's JSON response."""
        pass

    async def run(self, memory: AgentMemory) -> AgentMemory:
        """Common lifecycle: build prompt → call LLM → parse → write to memory."""
        prompt = self._build_prompt(memory)
        raw = await self.llm_svc.call(prompt)
        result = self._parse_output(raw)
        return self._write_to_memory(memory, result)
```

#### 🔵 Advanced

**Template Method Pattern for Agent Lifecycle**

`BaseAgent.run()` defines the invariant algorithm: prompt construction → LLM call → output parsing → memory write. Each concrete agent overrides only the variant steps (`_build_prompt`, `_parse_output`). This enforces consistent lifecycle management (every agent gets circuit breaker protection, token counting, latency logging) without code duplication. The `InvestigatorAgent` overrides `run()` entirely because it has a loop, but still uses `_build_prompt` and `_parse_output` for each step.

**Tool Registry as a Dispatch Table**

`ToolRegistry` maps tool names (strings from LLM output) to tool instances:

```python
registry = {
    "search_logs": LogTool(log_repo),
    "search_deploys": DeployTool(deploy_repo),
    "search_runbooks": RunbookTool(rag_service),
    "check_metrics": MetricsTool(metrics_client),
}
result = await registry[action].run(action_input)
```

The LLM outputs a string tool name. The registry performs the dispatch. This is the **Command pattern**: each tool call is a discrete, logged, replayable command. If the LLM outputs an unknown tool name, the registry raises `ToolNotFoundError` and the agent step fails cleanly — the error becomes the observation for the next step.

---

### 5.3 High-Level Design (HLD)

#### 🟢 Simple

HLD = how you break your system into big pieces and how those pieces talk to each other.

**Updated system decomposition:**

```
Sentinel
  ├── Alert ingestion        (what comes in)
  ├── Agent Orchestrator     (coordinates the four agents)
  │   ├── Classifier Agent   (triage)
  │   ├── Investigator Agent (evidence gathering with tools)
  │   ├── Hypothesis Agent   (reasoning)
  │   └── Responder Agent    (communication)
  ├── Tool Layer             (what agents can call)
  ├── RAG system             (called by RunbookTool)
  ├── Shared Memory          (AgentMemory — audit trail)
  └── Storage layer          (persistence)
```

**New tradeoffs to know:**

|Decision|Your Choice|Why|
|---|---|---|
|Single agent vs multi-agent|Multi-agent|One context window doing everything = lower quality per task|
|LangChain vs hand-rolled|Hand-rolled|You can explain every line. No black boxes in interviews.|
|Sequential vs parallel agents|Sequential|Each agent depends on prior output — parallelism doesn't apply|
|Fixed pipeline vs ReAct loop|ReAct loop|Adaptive evidence gathering beats fixed gather-everything|
|Max steps = 6|6|Prevents infinite loops; forces escalation on hard incidents|

#### 🔵 Advanced

**Why Multi-Agent Beats Single-Agent for This Problem**

A single agent doing classification + investigation + hypothesis + response has a context window that accumulates: alert + classification thoughts + all tool outputs + reasoning + response draft. At step 5, the LLM is reasoning about its final answer while distracted by 3,000 tokens of raw log output from step 1. Specialized agents with focused context windows produce better outputs per task — this is empirically validated in the multi-agent literature (CAMEL, AutoGen papers).

**Blackboard Pattern vs Message Passing**

Alternative: agents communicate via direct message passing (Investigator calls `HypothesisAgent.run(evidence=my_evidence)`). Problem: tight coupling, hard to add agents, no audit trail. Blackboard pattern: agents read from and write to `AgentMemory`. The `AgentOrchestrator` sequences agents; agents never reference each other. Adding a fifth agent requires only: (1) implement `BaseAgent`, (2) add one line to `AgentOrchestrator`. The full interaction history is preserved in `memory.agent_trace` for debugging and evaluation.

---

### 5.4 Distributed Systems

#### 🟢 Simple

Same distributed systems concepts as before, with one addition:

**Agent step recovery** — What if the worker crashes mid-investigation, between the Investigator and Hypothesis agents?

```python
# AgentMemory is checkpointed to Redis after each agent completes
# If the worker restarts, it can resume from the last checkpoint
# rather than starting the full investigation over
await redis.set(f"sentinel:memory:{incident_id}", memory.model_dump_json())
```

This is **exactly** how production agentic systems handle recovery — write state after each durable step.

#### 🔵 Advanced

**Checkpoint-Based Recovery for Agent Pipelines**

The `AgentOrchestrator` writes `AgentMemory` to Redis after each agent completes (not after each tool call — that would be too frequent). If the worker crashes between `HypothesisAgent` and `ResponderAgent`, the next worker to pick up the job reads the checkpoint and resumes at `ResponderAgent` — avoiding re-running expensive LLM calls. This is the **saga pattern** at the agent level: each agent is a compensatable transaction. If the full pipeline fails, the fallback is a deterministic response from whatever memory state exists.

**Agent Token Budget Management**

Each agent call contributes to a total token budget per investigation. The `AgentOrchestrator` tracks cumulative tokens via `memory.total_tokens_used` and can short-circuit the Investigator loop if the budget is exceeded — preventing runaway costs on pathological inputs. This is a **resource governor** pattern common in production LLM systems.

---

### 5.5 Database Design

#### 🟢 Simple

Same five tables plus one new one: `agent_traces`. This stores the full step-by-step reasoning for every investigation — every thought, every tool call, every observation. This is what shows up in the "Agent Trace" panel on the dashboard.

The feedback table is how you generate real accuracy metrics for your resume.

#### 🔵 Advanced

`agent_traces` stores `AgentMemory` as a JSONB column — the entire investigation history in one row. JSONB enables querying specific fields (`memory->'investigator_confidence'`) without full deserialization. The alternative (normalized tables for each thought-action-observation) is more queryable but introduces significant schema complexity for a portfolio project. JSONB is the correct tradeoff here.

---

### 5.6 RAG & GenAI

#### 🟢 Simple

Everything from before still applies. The agentic upgrade adds one important change:

**Before:** RAG always fetches the same chunks using the raw alert description as the query.

**After:** The Investigator Agent composes its own search query based on what it has already observed — "connection pool exhaustion DB timeout" instead of "Error rate on /charge endpoint exceeded 5%." The agent has context from previous tool calls; the raw alert doesn't.

**Agent-composed queries produce better retrieval.** That's the whole point.

#### 🔵 Advanced

**Query Rewriting as an Emergent Agent Behavior**

When the Investigator Agent calls `search_runbooks()`, it composes the query based on its accumulated observations — not the original alert. After observing 847 "connection refused" errors and a `pool_size: 20→5` deploy diff, its query becomes "database connection pool exhaustion timeout cascade" — a precise, evidence-informed search query. This is structurally identical to what Uber describes in their "Enhanced Agentic RAG" engineering blog: agents that rewrite queries based on retrieved context consistently outperform single-shot retrieval.

**Evaluation Implication**

The RAG evaluation in `tests/eval/test_rag_quality.py` now tests two things: (1) retrieval quality given good queries (same as before), and (2) query composition quality — does the agent produce queries that actually retrieve the right runbooks? The second metric requires golden query examples per incident type.

---

## 6. File Structure

```
sentinel/
├── README.md
├── docker-compose.yml
├── Makefile
├── pyproject.toml
├── .env.example
├── backend/
│   ├── main.py                         ← FastAPI app + lifespan
│   ├── api/
│   │   ├── routers/
│   │   │   ├── alerts.py               ← POST /alerts
│   │   │   ├── incidents.py            ← GET /incidents, GET /incidents/{id}
│   │   │   └── health.py
│   │   └── middleware/
│   │       ├── auth.py
│   │       └── rate_limiter.py         ← token bucket (per IP)
│   │
│   ├── agents/                         ← NEW: all agent code
│   │   ├── base_agent.py               ← BaseAgent ABC (Template Method)
│   │   ├── agent_memory.py             ← AgentMemory (Blackboard)
│   │   ├── agent_orchestrator.py       ← sequences the four agents
│   │   ├── classifier_agent.py         ← Agent 1: triage
│   │   ├── investigator_agent.py       ← Agent 2: ReAct loop
│   │   ├── hypothesis_agent.py         ← Agent 3: causal reasoning
│   │   └── responder_agent.py          ← Agent 4: engineer-ready output
│   │
│   ├── tools/                          ← NEW: what agents can call
│   │   ├── base_tool.py                ← BaseTool ABC
│   │   ├── log_tool.py                 ← wraps LogService
│   │   ├── deploy_tool.py              ← wraps DeployService
│   │   ├── runbook_tool.py             ← wraps RAGService
│   │   ├── metrics_tool.py             ← checks CPU/memory/error rate
│   │   └── tool_registry.py            ← dispatch table: name → tool instance
│   │
│   ├── services/                       ← mostly unchanged, now called by tools
│   │   ├── alert_service.py
│   │   ├── log_service.py
│   │   ├── deploy_service.py
│   │   ├── rag_service.py              ← called by RunbookTool
│   │   └── llm_service.py              ← shared by all agents
│   │
│   ├── db/
│   │   ├── database.py
│   │   ├── models.py                   ← + AgentTrace ORM model
│   │   └── repositories/
│   │       ├── incident_repo.py
│   │       ├── deploy_repo.py
│   │       └── log_repo.py
│   │
│   ├── workers/
│   │   └── investigation_worker.py     ← now calls AgentOrchestrator
│   │
│   ├── schemas/
│   │   ├── alert.py
│   │   ├── incident.py
│   │   ├── rag.py
│   │   ├── agent.py                    ← NEW: AgentMemory, ToolCall schemas
│   │   └── llm.py
│   │
│   └── config.py
├── data/
│   ├── runbooks/
│   └── sample_alerts/
├── scripts/
│   ├── build_index.py
│   ├── seed_db.py
│   └── simulate_incident.py
├── tests/
│   ├── unit/
│   │   ├── test_classifier_agent.py    ← NEW
│   │   ├── test_investigator_agent.py  ← NEW
│   │   └── test_tool_registry.py       ← NEW
│   ├── integration/
│   └── eval/
│       └── test_rag_quality.py
├── migrations/
└── frontend/
    ├── app/
    │   ├── page.tsx
    │   └── incidents/[id]/page.tsx
    └── components/
        ├── IncidentTable.tsx
        ├── InvestigationPanel.tsx
        └── AgentTracePanel.tsx         ← NEW: shows step-by-step reasoning
```

---

## 7. Data Models (LLD)

### 🟢 Simple

Same as before, plus new models for the agent system:

- `AgentMemory` = the shared notebook all agents read and write
- `ToolCall` = one tool invocation with its input and result
- `AgentStep` = one thought-action-observation triplet from the Investigator
- `ClassificationResult` = what the Classifier produces
- `HypothesisResult` = what the Hypothesis Agent produces
- `ResponderResult` = the final engineer-ready output

### 🔵 Advanced

`AgentMemory` is both the inter-agent communication mechanism (Blackboard pattern) and the audit trail. Every agent reads from it and writes to it. It is a Pydantic model that serializes cleanly to JSONB for Postgres storage. The `agent_trace` field provides a full replay of every reasoning step — valuable for debugging why an agent made a particular decision.

```python
# backend/schemas/agent.py
from datetime import datetime
from typing import Any
from uuid import UUID
from pydantic import BaseModel, Field
from .alert import AlertEvent


class ToolCall(BaseModel):
    """A single tool invocation — input and output."""
    tool_name: str
    tool_input: dict[str, Any]
    tool_output: str          # serialized result
    latency_ms: float
    called_at: datetime = Field(default_factory=datetime.utcnow)


class AgentStep(BaseModel):
    """One ReAct step: thought → action → observation."""
    step_number: int
    thought: str
    action: str               # tool name or "finish"
    action_input: dict[str, Any]
    observation: str          # tool output or "Investigation complete"
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class ClassificationResult(BaseModel):
    incident_type: str        # "database", "memory", "network", "deployment", "unknown"
    suggested_tools: list[str]
    skip_tools: list[str]
    priority: str             # "critical", "high", "medium"
    reasoning: str


class HypothesisResult(BaseModel):
    root_cause: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    causal_chain: list[str]   # ordered list of cause→effect steps
    alternative_hypotheses: list[dict]  # [{hypothesis, ruled_out_by}]
    evidence_used: list[str]


class ResponderResult(BaseModel):
    summary: str              # one sentence, for engineers who just woke up
    immediate_actions: list[str]   # numbered, with commands
    root_fix: str             # longer-term solution
    escalate: bool
    escalate_to: str | None
    notify: list[str]         # Slack channels / people


class AgentMemory(BaseModel):
    """
    Shared state across all agents — the Blackboard.
    Each agent reads prior state and appends its own output.
    Persisted to Redis (incremental) and Postgres (final).
    """
    incident_id: UUID
    alert: AlertEvent

    # Written by ClassifierAgent
    classification: ClassificationResult | None = None

    # Written by InvestigatorAgent
    agent_steps: list[AgentStep] = []
    tool_calls: list[ToolCall] = []
    evidence: list[str] = []          # clean evidence list for HypothesisAgent
    investigator_confidence: float = 0.0

    # Written by HypothesisAgent
    hypothesis: HypothesisResult | None = None

    # Written by ResponderAgent
    response: ResponderResult | None = None

    # System metadata
    total_llm_calls: int = 0
    total_tokens_used: int = 0
    started_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None
```

```python
# backend/schemas/alert.py — unchanged from before
from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import UUID
from pydantic import BaseModel, Field, field_validator
import hashlib

class Severity(str, Enum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"

class AlertEvent(BaseModel):
    service_name: str = Field(..., description="e.g. 'payments-api'")
    severity: Severity
    title: str = Field(..., max_length=200)
    description: str = Field(..., max_length=2000)
    error_type: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    source: str = Field(default="manual")
    detected_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("service_name")
    @classmethod
    def normalize_service_name(cls, v: str) -> str:
        return v.lower().strip().replace(" ", "-")

    @property
    def fingerprint(self) -> str:
        key = f"{self.service_name}:{self.error_type}:{self.detected_at.strftime('%Y%m%d%H')}"
        return hashlib.md5(key.encode()).hexdigest()

class AlertResponse(BaseModel):
    incident_id: UUID
    status: str
    message: str

class InvestigationResult(BaseModel):
    incident_id: UUID
    root_cause: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    evidence: list[str]
    suggested_fix: str
    runbook_sources: list[str]
    deploy_correlation: Optional[str] = None
    escalate: bool = False
    investigation_time_seconds: float
    llm_model_used: str
    agent_steps_count: int = 0        # NEW: how many ReAct steps
    total_llm_calls: int = 0          # NEW: total calls across all agents
```

---

## 8. Core Agent Code

### 8.1 BaseAgent and AgentOrchestrator

#### 🟢 Simple

`BaseAgent` is the template every agent follows: build a prompt from memory → call the LLM → parse the JSON response → write the result back to memory. Each agent only customizes the prompt and the parsing — the lifecycle is the same for all of them.

`AgentOrchestrator` runs all four agents in sequence, passing the same `AgentMemory` object through each one.

**Two hallucination defenses live here:**

- **Defense 4 — Confidence calibration:** After the Investigator finishes, the Orchestrator checks whether the claimed confidence is plausible given the number of tool calls made. High confidence from a single tool call is suspicious — the Orchestrator caps it at 0.5.
- **Defense 5 — Schema enforcement:** Every agent's LLM output is validated against a Pydantic model before entering the shared memory. If the LLM returns `confidence: "high"` instead of `confidence: 0.85`, it's caught immediately and the agent fails cleanly rather than silently corrupting downstream agents.

#### 🔵 Advanced

`BaseAgent` implements the **Template Method pattern**: `run()` is the invariant algorithm, `_build_prompt()` and `_parse_output()` are the variant hooks. Every concrete agent gets circuit breaker protection, token tracking, and latency logging for free by inheriting `run()`. `InvestigatorAgent` overrides `run()` because it has a loop, but its per-step logic still uses the template methods. The `AgentOrchestrator` checkpoints `AgentMemory` to Redis after each agent completes — enabling recovery if the worker crashes mid-pipeline.

**Defense 4 — Confidence calibration:** A well-calibrated investigator should not claim 91% confidence after one tool call — one data point is never enough corroboration. The Orchestrator enforces this heuristic: `tool_call_count < 2 AND confidence > 0.7 → cap at 0.5`. This catches the common LLM failure mode of "confident on thin evidence" before it reaches the HypothesisAgent.

**Defense 5 — Schema enforcement at every boundary:** `BaseAgent.run()` wraps `_parse_output()` in a try/except. A `ValidationError` from Pydantic means the LLM produced structurally invalid output — wrong field types, missing required fields, out-of-range values. This fails loudly with a logged error rather than propagating a corrupted Pydantic model through the pipeline. The `AgentOutputError` exception bubbles up to the `AgentOrchestrator`, which marks the incident as `failed` and the checkpoint ensures no partial state is lost.

```python
# backend/agents/base_agent.py
import logging, time
from abc import ABC, abstractmethod
from typing import Any
from ..schemas.agent import AgentMemory
from ..services.llm_service import LLMService

logger = logging.getLogger(__name__)

class BaseAgent(ABC):
    """
    Template Method pattern: run() defines the invariant lifecycle.
    Subclasses override _build_prompt() and _parse_output().
    """
    name: str = "BaseAgent"

    def __init__(self, llm_svc: LLMService):
        self.llm_svc = llm_svc

    @abstractmethod
    def _build_prompt(self, memory: AgentMemory) -> str:
        """Build the user prompt from current memory state."""
        pass

    @abstractmethod
    def _parse_output(self, raw: dict) -> Any:
        """Parse the LLM's JSON output into a typed result."""
        pass

    @abstractmethod
    def _write_to_memory(self, memory: AgentMemory, result: Any) -> AgentMemory:
        """Write this agent's result to the shared memory object."""
        pass

    async def run(self, memory: AgentMemory) -> AgentMemory:
        """Invariant lifecycle: prompt → LLM call → parse → memory write."""
        start = time.monotonic()
        logger.info(f"[{self.name}] Starting for incident {memory.incident_id}")

        prompt = self._build_prompt(memory)
        raw = await self.llm_svc.call_structured(prompt, system_prompt=self._system_prompt())

        # Defense 5: Schema enforcement at every agent boundary
        # If the LLM returns malformed output (e.g. confidence: "high" instead of 0.85),
        # Pydantic catches it here — agent fails cleanly rather than silently corrupting
        # downstream agents with bad data
        try:
            result = self._parse_output(raw)
        except Exception as e:
            logger.error(f"[{self.name}] Output schema violation: {e}. Raw output: {raw}")
            raise AgentOutputError(f"{self.name} produced invalid output: {e}")

        memory = self._write_to_memory(memory, result)

        elapsed_ms = (time.monotonic() - start) * 1000
        memory.total_llm_calls += 1
        logger.info(f"[{self.name}] Completed in {elapsed_ms:.0f}ms")
        return memory

    def _system_prompt(self) -> str:
        """Override in each agent to provide a specialized system prompt."""
        return "You are an expert SRE agent. Respond with ONLY valid JSON."
```

```python
# backend/agents/agent_orchestrator.py
import logging
from uuid import UUID
import redis.asyncio as aioredis
from ..schemas.agent import AgentMemory
from ..schemas.alert import AlertEvent
from ..agents.classifier_agent import ClassifierAgent
from ..agents.investigator_agent import InvestigatorAgent
from ..agents.hypothesis_agent import HypothesisAgent
from ..agents.responder_agent import ResponderAgent
from ..db.repositories.incident_repo import IncidentRepository

logger = logging.getLogger(__name__)


class AgentOrchestrator:
    """
    Sequences the four agents, passing AgentMemory through each.
    Checkpoints memory to Redis after each agent for crash recovery.
    """

    def __init__(self, classifier, investigator, hypothesis, responder,
                 incident_repo: IncidentRepository, redis: aioredis.Redis):
        self.classifier = classifier
        self.investigator = investigator
        self.hypothesis = hypothesis
        self.responder = responder
        self.incident_repo = incident_repo
        self.redis = redis

    async def run(self, alert: AlertEvent, incident_id: UUID) -> AgentMemory:
        memory = AgentMemory(incident_id=incident_id, alert=alert)
        await self.incident_repo.update_status(incident_id, "investigating")

        try:
            # Agent 1: Classify
            memory = await self.classifier.run(memory)
            await self._checkpoint(memory)

            # Agent 2: Investigate (ReAct loop)
            memory = await self.investigator.run(memory)
            await self._checkpoint(memory)

            # Defense 4: Confidence calibration
            # High confidence with thin evidence is suspicious.
            # Fewer than 2 real tool calls should not produce > 0.7 confidence —
            # a single data point is never enough to be 91% certain.
            tool_call_count = len(memory.tool_calls)
            if tool_call_count < 2 and memory.investigator_confidence > 0.7:
                logger.warning(
                    f"[Orchestrator] Confidence {memory.investigator_confidence:.2f} "
                    f"with only {tool_call_count} tool call(s). Capping at 0.5."
                )
                memory.investigator_confidence = 0.5

            # Agent 3: Form hypothesis
            memory = await self.hypothesis.run(memory)
            await self._checkpoint(memory)

            # Agent 4: Write response
            memory = await self.responder.run(memory)

            # Persist final result
            await self.incident_repo.save_agent_result(memory)
            await self.incident_repo.update_status(incident_id, "resolved")

        except Exception as e:
            logger.error(f"Agent pipeline failed for {incident_id}: {e}", exc_info=True)
            await self.incident_repo.update_status(incident_id, "failed")
            raise

        return memory

    async def _checkpoint(self, memory: AgentMemory):
        """Write memory to Redis after each agent for crash recovery."""
        key = f"sentinel:memory:{memory.incident_id}"
        await self.redis.setex(key, 3600, memory.model_dump_json())
```

---

### 8.2 ClassifierAgent

#### 🟢 Simple

The first agent. Fast and cheap — one LLM call. Its only job: look at the alert and decide what kind of incident it is, and which tools are relevant. A DB timeout should not trigger Kubernetes pod checking. This classification prevents the Investigator from wasting steps on irrelevant tools.

#### 🔵 Advanced

The Classifier uses a small, focused prompt with a fixed output schema — it doesn't need deep reasoning, just reliable categorization. `temperature: 0.0` for maximum determinism. The `skip_tools` output directly configures the `ToolRegistry` visible to the Investigator — tools in `skip_tools` are excluded from the agent's available actions, making it impossible for the Investigator to call them regardless of what its prompt says.

```python
# backend/agents/classifier_agent.py
from ..agents.base_agent import BaseAgent
from ..schemas.agent import AgentMemory, ClassificationResult

CLASSIFIER_SYSTEM = """You are an incident classifier. Analyze the alert and output ONLY valid JSON.
{
  "incident_type": "database|memory|network|deployment|unknown",
  "suggested_tools": ["search_logs", "search_deploys", "search_runbooks"],
  "skip_tools": ["check_kubernetes_pods"],
  "priority": "critical|high|medium",
  "reasoning": "one sentence explaining your classification"
}
Available tools: search_logs, search_deploys, search_runbooks, check_metrics, check_related_services"""


class ClassifierAgent(BaseAgent):
    name = "ClassifierAgent"

    def _system_prompt(self) -> str:
        return CLASSIFIER_SYSTEM

    def _build_prompt(self, memory: AgentMemory) -> str:
        alert = memory.alert
        return f"""Classify this production incident:
Service: {alert.service_name}
Severity: {alert.severity}
Title: {alert.title}
Description: {alert.description}
Error type: {alert.error_type}"""

    def _parse_output(self, raw: dict) -> ClassificationResult:
        return ClassificationResult(**raw)

    def _write_to_memory(self, memory: AgentMemory, result: ClassificationResult) -> AgentMemory:
        memory.classification = result
        return memory
```

---

### 8.3 InvestigatorAgent (ReAct Loop)

#### 🟢 Simple

This is the most important agent. It runs a loop: think about what to do → call a tool → read the result → decide whether to keep going or stop.

It starts with the tools the Classifier said were relevant. It stops when it's confident enough (confidence > 0.85) or when it hits the maximum steps limit (6).

Every thought, action, and observation is recorded in `AgentMemory` — so you can see exactly why it made each decision.

**Two hallucination defenses live here:**

- **Defense 1 — Evidence grounding:** When the agent calls `finish`, every evidence point it claims is cross-checked against what the tools actually returned. Invented evidence gets dropped and confidence is penalized proportionally. An agent that claims it saw a CPU spike when it never called `check_metrics` will have that claim removed.
- **Defense 2 — Tool name validation:** If the agent calls a tool that doesn't exist, it gets a corrective error message as its observation and can recover by choosing a valid tool — instead of crashing the whole pipeline.

#### 🔵 Advanced

The ReAct loop is hand-rolled: at each step, the LLM receives the full accumulated thought-action-observation history in its message list, outputs `{thought, action, action_input}`, the `ToolRegistry` dispatches the tool, the observation is appended to the history, and the LLM decides the next action. This is the core ReAct pattern from the 2022 Yao et al. paper. The accumulating context gives the agent memory of what it has tried — without this, it would repeat the same tool call indefinitely.

`finish` is a pseudo-tool that terminates the loop. The LLM calls `finish` when it has sufficient evidence. `MAX_STEPS = 6` is a hard guardrail that triggers escalation if the LLM never calls `finish`.

**Defense 1 — Evidence grounding (the most critical defense):** The `_validate_evidence()` method cross-checks each evidence claim from the `finish` action against the concatenated text of all real tool observations stored in `memory.tool_calls`. A claim with fewer than 2 meaningful term matches against real observations is dropped and a warning is logged. Confidence is then penalized by `grounding_ratio = len(validated) / len(raw)` — an agent that fabricated half its evidence gets its confidence halved. This is the most important defense because the HypothesisAgent only sees `memory.evidence` — if that list is clean and grounded, the hypothesis will be grounded.

**Defense 2 — Tool name validation:** `ToolNotFoundError` from the registry is caught and converted into a corrective observation string rather than propagating as an exception. The agent sees `"ERROR: Tool 'check_prometheus' does not exist. Available: search_logs, search_deploys..."` as its next observation and can choose a valid tool. Two consecutive hallucinated tool names deplete steps quickly, triggering `MAX_STEPS` → escalation — the safe outcome.

```python
# backend/agents/investigator_agent.py
import json, logging, time
from datetime import datetime
from ..agents.base_agent import BaseAgent
from ..schemas.agent import AgentMemory, AgentStep, ToolCall
from ..tools.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)
MAX_STEPS = 6

INVESTIGATOR_SYSTEM = """You are an SRE investigator. At each step output ONLY valid JSON:
{
  "thought": "your reasoning about what to do next",
  "action": "tool_name or finish",
  "action_input": {"param": "value"}
}
When action is "finish": action_input must be {"confidence": 0.0-1.0, "evidence": ["..."]}
Stop when confidence > 0.85 or when you have tried all relevant tools."""


class InvestigatorAgent(BaseAgent):
    name = "InvestigatorAgent"

    def __init__(self, llm_svc, tool_registry: ToolRegistry):
        super().__init__(llm_svc)
        self.tool_registry = tool_registry

    def _system_prompt(self) -> str:
        return INVESTIGATOR_SYSTEM

    def _build_prompt(self, memory: AgentMemory) -> str:
        """Build initial prompt from alert + classification."""
        alert = memory.alert
        tools = memory.classification.suggested_tools if memory.classification else []
        return f"""Investigate this incident:
Service: {alert.service_name} | Severity: {alert.severity}
Title: {alert.title}
Description: {alert.description}

Available tools: {', '.join(tools)}
Start investigating. Call tools to gather evidence."""

    def _parse_output(self, raw: dict) -> dict:
        return raw  # InvestigatorAgent handles its own parsing in the loop

    def _write_to_memory(self, memory: AgentMemory, result) -> AgentMemory:
        return memory  # handled inside run()

    async def run(self, memory: AgentMemory) -> AgentMemory:
        """Override run() — InvestigatorAgent has a loop, not a single call."""
        # Configure tool registry based on Classifier output
        available_tools = memory.classification.suggested_tools if memory.classification else []
        skip_tools = memory.classification.skip_tools if memory.classification else []
        self.tool_registry.configure(available=available_tools, skip=skip_tools)

        # Build message history — accumulates thought-action-observation
        messages = [
            {"role": "system", "content": self._system_prompt()},
            {"role": "user", "content": self._build_prompt(memory)},
        ]

        for step_num in range(1, MAX_STEPS + 1):
            step_start = time.monotonic()

            # LLM decides next action
            raw = await self.llm_svc.call_raw(messages)
            parsed = json.loads(raw["choices"][0]["message"]["content"])
            thought = parsed.get("thought", "")
            action = parsed.get("action", "")
            action_input = parsed.get("action_input", {})

            # Terminal condition
            if action == "finish":
                confidence = action_input.get("confidence", 0.0)
                raw_evidence = action_input.get("evidence", [])

                # Defense 1: Ground evidence against actual tool observations
                # The agent might claim evidence it never actually gathered.
                # Cross-check every evidence point against real tool outputs.
                # Anything not traceable to an actual observation gets dropped.
                validated_evidence = self._validate_evidence(raw_evidence, memory.tool_calls)

                if len(raw_evidence) > 0:
                    grounding_ratio = len(validated_evidence) / len(raw_evidence)
                    # Penalize confidence proportionally to how much evidence was fabricated
                    confidence = confidence * grounding_ratio
                    if grounding_ratio < 1.0:
                        logger.warning(
                            f"[InvestigatorAgent] {len(raw_evidence) - len(validated_evidence)} "
                            f"evidence point(s) dropped as ungrounded. "
                            f"Confidence adjusted to {confidence:.2f}"
                        )

                memory.evidence = validated_evidence
                memory.investigator_confidence = confidence
                logger.info(f"[InvestigatorAgent] Finished at step {step_num}, confidence={confidence:.2f}")
                break

            # Execute tool
            # Defense 2: Treat hallucinated tool names as corrective observations
            # rather than crashes. The agent sees its mistake and can recover
            # on the next step by choosing a valid tool.
            try:
                observation = await self.tool_registry.execute(action, action_input)
            except ToolNotFoundError:
                observation = (
                    f"ERROR: Tool '{action}' does not exist. "
                    f"Available tools: {list(self.tool_registry._active_tools.keys())}. "
                    f"You must call one of the available tools or call 'finish'."
                )
                logger.warning(f"[InvestigatorAgent] Hallucinated tool name: '{action}'")
            except Exception as e:
                observation = f"Tool error: {str(e)}"

            # Record step
            step = AgentStep(
                step_number=step_num,
                thought=thought,
                action=action,
                action_input=action_input,
                observation=observation,
                timestamp=datetime.utcnow(),
            )
            memory.agent_steps.append(step)
            memory.tool_calls.append(ToolCall(
                tool_name=action,
                tool_input=action_input,
                tool_output=observation,
                latency_ms=(time.monotonic() - step_start) * 1000,
            ))

            # Append to message history — this gives the agent memory
            messages.append({"role": "assistant", "content": json.dumps(parsed)})
            messages.append({"role": "user", "content": f"Observation: {observation}\nContinue investigating."})

            logger.info(f"[InvestigatorAgent] Step {step_num}: {action}")

        else:
            # MAX_STEPS hit without finish — escalate
            logger.warning(f"[InvestigatorAgent] MAX_STEPS reached, escalating")
            memory.investigator_confidence = 0.0

        memory.total_llm_calls += step_num
        return memory

    def _validate_evidence(self, evidence: list[str], tool_calls: list) -> list[str]:
        """
        Defense 1: Cross-check each evidence claim against actual tool outputs.

        The agent can hallucinate evidence in the 'finish' action — claiming it
        observed something it never actually checked. This method compares key
        terms from each evidence point against the concatenated text of all real
        tool observations. Evidence with fewer than 2 meaningful term matches
        is dropped and logged as ungrounded.

        Example:
          Real observation: "847 connection refused errors in last 30min"
          Claim: "connection refused errors spiked" → 2 matches → KEPT
          Claim: "CPU spike detected on DB server" → 0 matches → DROPPED
        """
        import re
        STOP_WORDS = {"the", "a", "an", "in", "at", "of", "was", "is",
                      "caused", "by", "this", "that", "and", "or", "to",
                      "for", "with", "on", "from", "has", "had"}

        # Build a single string of all real tool observations
        all_observations = " ".join(tc.tool_output for tc in tool_calls).lower()

        validated = []
        for claim in evidence:
            words = set(re.findall(r'\w+', claim.lower()))
            meaningful = words - STOP_WORDS
            overlap = sum(1 for w in meaningful if w in all_observations)

            if overlap >= 2:
                validated.append(claim)
            else:
                logger.warning(
                    f"[InvestigatorAgent] Dropped ungrounded evidence "
                    f"(overlap={overlap}): '{claim[:80]}...'"
                )

        return validated
```

---

### 8.4 HypothesisAgent

#### 🟢 Simple

The Hypothesis Agent receives the clean evidence list from the Investigator and forms a root cause. It doesn't call any tools. Its only job is to reason analytically — "given this evidence, what is the most likely cause?"

It also generates alternative hypotheses and rules them out — this is the "causal chain" that makes the output trustworthy.

**Defense 3 — Hypothesis grounding lives here:** After the LLM produces a root cause, the code checks whether the key terms actually appear in the evidence list. If the root cause mentions something like "JVM memory leak" but the evidence only talks about connection pool sizes, the root cause is flagged as ungrounded and confidence is capped at 0.4 — which automatically triggers escalation to a human. This prevents a confident-sounding but completely fabricated causal chain from reaching the Responder.

#### 🔵 Advanced

The Hypothesis Agent's context window contains only `memory.evidence` — a clean list of bullet points, not raw tool outputs. This is the key design decision: the Investigator is responsible for distilling its observations into clean evidence; the Hypothesis Agent reasons over the distilled version. Giving the Hypothesis Agent raw log lines or deploy diffs would degrade its reasoning quality — it would spend context tokens processing formatting noise rather than reasoning causally. The `causal_chain` output (ordered cause→effect steps) directly maps to the demo output: "deploy reduced pool size → pool exhausted under load → connection refused errors → error rate spike."

**Defense 3 — Hypothesis grounding:** `_validate_hypothesis()` computes term overlap between key words in the `root_cause` string and the concatenated `memory.evidence` text. Fewer than 3 meaningful word matches → confidence capped at 0.4 → `escalate: True` flows through to the Responder automatically. This is the last line of defense before a wrong answer reaches an on-call engineer. The cap at 0.4 (below the 0.6 escalation threshold) ensures no ungrounded hypothesis ever produces a non-escalated response.

```python
# backend/agents/hypothesis_agent.py
from ..agents.base_agent import BaseAgent
from ..schemas.agent import AgentMemory, HypothesisResult

HYPOTHESIS_SYSTEM = """You are an expert SRE analyst. Given evidence from an investigation,
form a root cause hypothesis. Output ONLY valid JSON:
{
  "root_cause": "one clear sentence",
  "confidence": 0.87,
  "causal_chain": [
    "step 1: what changed",
    "step 2: direct effect",
    "step 3: cascading effect",
    "step 4: observed symptom"
  ],
  "alternative_hypotheses": [
    {"hypothesis": "alternative cause", "ruled_out_by": "evidence that contradicts it"}
  ],
  "evidence_used": ["evidence point 1", "evidence point 2"]
}
confidence: 0.0-1.0. Be conservative — use < 0.7 if evidence is ambiguous."""


class HypothesisAgent(BaseAgent):
    name = "HypothesisAgent"

    def _system_prompt(self) -> str:
        return HYPOTHESIS_SYSTEM

    def _build_prompt(self, memory: AgentMemory) -> str:
        evidence_text = "\n".join(f"- {e}" for e in memory.evidence) or "No evidence gathered."
        return f"""Form a root cause hypothesis from this evidence:

Service: {memory.alert.service_name}
Alert: {memory.alert.title}

Evidence gathered:
{evidence_text}

What is the most likely root cause?"""

    def _parse_output(self, raw: dict) -> HypothesisResult:
        return HypothesisResult(**raw)

    def _write_to_memory(self, memory: AgentMemory, result: HypothesisResult) -> AgentMemory:
        # Defense 3: Verify the hypothesis is grounded in actual evidence
        # The HypothesisAgent can produce a confident-sounding causal chain
        # that has nothing to do with what the Investigator actually found.
        # Check term overlap between root_cause and the evidence list.
        result = self._validate_hypothesis(result, memory.evidence)
        memory.hypothesis = result
        return memory

    def _validate_hypothesis(
        self, hypothesis: HypothesisResult, evidence: list[str]
    ) -> HypothesisResult:
        """
        Defense 3: Check that the root cause is grounded in at least one evidence point.

        If the root cause has very low term overlap with the actual evidence,
        the hypothesis is likely hallucinated. We cap confidence at 0.4 in
        that case — which triggers escalate=True in the ResponderAgent.

        Example:
          Evidence: ["pool_size reduced 20→5", "847 connection refused errors"]
          Root cause: "DB pool exhausted under load" → overlap high → PASS
          Root cause: "JVM memory leak in application layer" → overlap=0 → CAPPED
        """
        import re
        STOP_WORDS = {"the", "a", "an", "in", "at", "of", "was", "is",
                      "caused", "by", "this", "that", "and", "or", "to"}

        evidence_text = " ".join(evidence).lower()
        root_cause_words = set(re.findall(r'\w+', hypothesis.root_cause.lower()))
        meaningful = root_cause_words - STOP_WORDS
        overlap = sum(1 for w in meaningful if w in evidence_text)

        if overlap < 3:
            logger.warning(
                f"[HypothesisAgent] Root cause appears ungrounded "
                f"(overlap={overlap}). Capping confidence at 0.4. "
                f"Root cause: '{hypothesis.root_cause[:80]}'"
            )
            hypothesis.confidence = min(hypothesis.confidence, 0.4)
            # confidence < 0.6 triggers escalate=True in ResponderAgent

        return hypothesis
```

---

### 8.5 ResponderAgent

#### 🟢 Simple

The last agent. It reads the hypothesis and writes something an on-call engineer can actually use at 3 AM — short summary, numbered commands, escalation decision. No analysis. No explanation. Just: do this, then this, then this.

#### 🔵 Advanced

The Responder Agent has the narrowest context window of the four agents — it only reads `memory.hypothesis`, not the full evidence or tool calls. This is intentional: the Responder's job is communication, not analysis. A responder given the full investigation context tends to reproduce analytical reasoning in the output — which is wrong for 3 AM incident response. The output format (`immediate_actions` as a numbered list of commands) is designed to be copy-pasted directly into a terminal. `escalate: true` routes to a human via PagerDuty or Slack — the Responder is the last gate before human escalation.

```python
# backend/agents/responder_agent.py
from ..agents.base_agent import BaseAgent
from ..schemas.agent import AgentMemory, ResponderResult

RESPONDER_SYSTEM = """You are writing a response for an on-call engineer woken at 3 AM.
Be terse. Lead with commands. No analysis. Output ONLY valid JSON:
{
  "summary": "one sentence: what happened and why",
  "immediate_actions": [
    "1. command or step",
    "2. command or step"
  ],
  "root_fix": "longer-term fix once incident is resolved",
  "escalate": false,
  "escalate_to": null,
  "notify": ["#payments-oncall"]
}
escalate: true if confidence < 0.6 or if fix requires human judgment."""


class ResponderAgent(BaseAgent):
    name = "ResponderAgent"

    def _system_prompt(self) -> str:
        return RESPONDER_SYSTEM

    def _build_prompt(self, memory: AgentMemory) -> str:
        h = memory.hypothesis
        if not h:
            return f"Investigation failed for {memory.alert.service_name}. Write escalation response."
        return f"""Write an on-call response for this hypothesis:

Service: {memory.alert.service_name} | Severity: {memory.alert.severity}
Root cause: {h.root_cause}
Confidence: {h.confidence:.0%}
Causal chain:
{chr(10).join(f"  {step}" for step in h.causal_chain)}"""

    def _parse_output(self, raw: dict) -> ResponderResult:
        return ResponderResult(**raw)

    def _write_to_memory(self, memory: AgentMemory, result: ResponderResult) -> AgentMemory:
        memory.response = result
        # Escalate if hypothesis confidence is too low, regardless of responder
        if memory.hypothesis and memory.hypothesis.confidence < 0.6:
            memory.response.escalate = True
        return memory
```

---

### 8.6 Tool Layer

#### 🟢 Simple

Tools are what the Investigator Agent can call. Each tool has a name (string), a description (so the LLM knows what it does), and a `run()` method that does the actual work.

The `ToolRegistry` is a dictionary mapping tool names to tool instances. When the Investigator says `action: "search_logs"`, the registry calls `LogTool.run(action_input)`.

#### 🔵 Advanced

`BaseTool` defines the interface. `ToolRegistry` implements the **Dispatch Table pattern** — a dictionary from string names to callables — which is how every production tool-use system routes LLM-selected actions to implementations. The `configure(available, skip)` method restricts the registry based on the Classifier's output, making it impossible for the Investigator to call `check_kubernetes_pods` on a database incident regardless of what its prompt says. This is a **capability restriction** pattern — the agent's available actions are bounded by the classifier's routing decision, not just by the agent's own prompt.

```python
# backend/tools/base_tool.py
from abc import ABC, abstractmethod
from typing import Any

class BaseTool(ABC):
    name: str
    description: str  # shown to the LLM so it knows when to use this tool

    @abstractmethod
    async def run(self, input: dict[str, Any]) -> str:
        """Execute the tool and return observation as string."""
        pass
```

```python
# backend/tools/log_tool.py
from .base_tool import BaseTool
from ..services.log_service import LogService
from datetime import datetime

class LogTool(BaseTool):
    name = "search_logs"
    description = "Search recent error logs for a service. Returns top error patterns."

    def __init__(self, log_svc: LogService):
        self.log_svc = log_svc

    async def run(self, input: dict) -> str:
        service = input.get("service_name")
        window = int(input.get("window_minutes", 30))
        since = datetime.utcnow()
        logs = await self.log_svc.fetch_recent(service, since, limit=200)
        return self.log_svc.summarize(logs, max_lines=20)
```

```python
# backend/tools/runbook_tool.py
from .base_tool import BaseTool
from ..services.rag_service import RAGService

class RunbookTool(BaseTool):
    name = "search_runbooks"
    description = "Search runbooks for relevant procedures. Use queries specific to the error pattern."

    def __init__(self, rag_svc: RAGService):
        self.rag_svc = rag_svc

    async def run(self, input: dict) -> str:
        query = input.get("query", "")
        service = input.get("service_name")
        chunks = await self.rag_svc.retrieve(query, service_hint=service, top_k=3)
        if not chunks:
            return "No relevant runbooks found."
        return "\n\n".join(
            f"[{c.source_document}]:\n{c.text[:400]}..."
            for c in chunks
        )
```

```python
# backend/tools/tool_registry.py
import logging
from typing import Any
from .base_tool import BaseTool

logger = logging.getLogger(__name__)

class ToolNotFoundError(Exception):
    pass

class ToolRegistry:
    """Dispatch table: tool name (string) → tool instance."""

    def __init__(self, tools: list[BaseTool]):
        self._all_tools = {t.name: t for t in tools}
        self._active_tools = dict(self._all_tools)  # starts with all tools

    def configure(self, available: list[str], skip: list[str]):
        """Restrict available tools based on Classifier output."""
        if available:
            self._active_tools = {
                name: tool for name, tool in self._all_tools.items()
                if name in available and name not in skip
            }
        logger.info(f"ToolRegistry configured: {list(self._active_tools.keys())}")

    def tool_descriptions(self) -> str:
        """Return tool descriptions for inclusion in agent prompt."""
        return "\n".join(
            f"- {name}: {tool.description}"
            for name, tool in self._active_tools.items()
        )

    async def execute(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        if tool_name not in self._active_tools:
            raise ToolNotFoundError(f"Tool '{tool_name}' not available. Choose from: {list(self._active_tools.keys())}")
        tool = self._active_tools[tool_name]
        result = await tool.run(tool_input)
        return result
```

---

### 8.7 Unchanged Services (LogService, DeployService, RAGService, LLMService, AlertService, Worker)

#### 🟢 Simple

These services are unchanged from the original version. The only difference: instead of `InvestigationService` calling them directly, the tools now call them. `LogTool` calls `LogService`. `RunbookTool` calls `RAGService`. The services don't know or care who's calling them.

The worker now calls `AgentOrchestrator` instead of `InvestigationService` — that's the only change to the worker.

#### 🔵 Advanced

The service layer remains unchanged because the tools are thin adapters — they translate between the agent's JSON `action_input` format and the service's typed function signatures. This is the **Adapter pattern**: `LogTool.run({"service_name": "payments-api", "window_minutes": 30})` adapts the dict-based tool interface to `LogService.fetch_recent(service_name, since, limit)`. The services retain their strong typing; the tools handle the impedance mismatch between untyped agent outputs and typed service interfaces.

The worker change is minimal:

```python
# In investigation_worker.py — only this block changes:
# Before:
svc = InvestigationService(log_svc=..., deploy_svc=..., rag_svc=..., llm_svc=..., incident_repo=...)
await svc.investigate(alert, incident_id)

# After:
orchestrator = AgentOrchestrator(
    classifier=ClassifierAgent(llm_svc),
    investigator=InvestigatorAgent(llm_svc, tool_registry),
    hypothesis=HypothesisAgent(llm_svc),
    responder=ResponderAgent(llm_svc),
    incident_repo=incident_repo,
    redis=redis_client,
)
await orchestrator.run(alert, incident_id)
```

---

### 8.8 Hallucination Defense Summary

#### 🟢 Simple

Agents hallucinate differently from single LLM calls — one bad step cascades through the whole pipeline. Here's a map of all six defenses and where they live:

|Defense|Where|What it catches|
|---|---|---|
|**1. Evidence grounding**|`InvestigatorAgent._validate_evidence()`|Agent claiming it saw something it never checked|
|**2. Tool name validation**|`ToolRegistry.execute()` → corrective observation|Agent calling a non-existent tool name|
|**3. Hypothesis grounding**|`HypothesisAgent._validate_hypothesis()`|Root cause unconnected to actual evidence|
|**4. Confidence calibration**|`AgentOrchestrator.run()` post-investigator check|Overconfidence on thin evidence (< 2 tool calls)|
|**5. Schema enforcement**|`BaseAgent.run()` Pydantic validation|Malformed LLM output (wrong types, missing fields)|
|**6. Grounding eval suite**|`tests/eval/test_agent_grounding.py`|Systematic hallucination across prompt/model changes|

**The cascade rule:** If any defense catches a problem, confidence drops below 0.6, which forces `escalate: True` in the Responder — the worst case is always a human gets paged, never a wrong fix executed silently on a live system.

#### 🔵 Advanced

The six defenses operate at different granularities and catch different failure modes:

**Defenses 1 and 3** are **grounding checks** — they verify that agent outputs are traceable to real observations. Defense 1 operates on the raw evidence list before it enters `AgentMemory`; Defense 3 operates on the synthesized hypothesis after reasoning. Together they form a sandwich: ground the inputs to the HypothesisAgent, then ground the HypothesisAgent's output.

**Defense 2** is a **recovery mechanism** — it converts a hard crash (unknown tool name) into a soft error (corrective observation). This is the difference between an agent pipeline that fails on the first hallucinated tool call versus one that gives the agent a chance to self-correct.

**Defense 4** is a **calibration heuristic** — it catches the systematic LLM tendency to report high confidence regardless of evidence quantity. One tool call giving 91% confidence is epistemically unjustified; the Orchestrator enforces epistemic humility in code.

**Defense 5** is a **type system boundary** — it's the equivalent of a compiled language's type checker, applied at the LLM output boundary where raw JSON meets typed Python objects. It prevents silent data corruption from propagating through the Blackboard.

**Defense 6** is the only **offline defense** — it runs on known inputs with known expected outputs. It's the only way to catch _systematic_ hallucination patterns (e.g., the model always invents CPU metrics for DB incidents) rather than individual hallucinations caught at runtime.

The layered approach is important: no single defense is sufficient. Defense 1 only catches evidence fabrication; Defense 3 only catches hypothesis fabrication; Defense 5 only catches structural malformation. The defenses are complementary, not redundant.

---

### 8.9 Token Cost Optimizations

#### 🟢 Simple

Every LLM call costs tokens. Across a full investigation (5–9 LLM calls), Sentinel spends roughly 5,000–8,000 tokens — about $0.003–$0.008 at GPT-4o-mini rates. That's cheap per investigation but adds up at scale. Three things you can do to reduce it — beyond what's already built — are classifier caching, message history pruning, and model tiering.

**What's already built (no changes needed):**

- `LogService.summarize()` shrinks 200 raw log lines to 20 patterns before any LLM sees them — saves ~1,800 tokens per investigation
- `RunbookTool` returns `c.text[:400]` per chunk, not the full chunk — caps runbook context at a predictable size

**What needs to be added:**

**Optimization 1 — Classifier caching:** The same DB timeout on payments-api will always classify the same way — incident_type: "database", same tool list, every time. There's no reason to pay for an LLM call to re-classify a pattern you've seen before. Cache the result in Redis for 1 hour.

**Optimization 2 — Message history pruning:** The Investigator's context window grows with every step. By step 4, the message history can be 2,000+ tokens of accumulated tool observations. Most of that early context is no longer useful — the agent already acted on it. Prune it to a one-line summary after step 3.

**Optimization 3 — Model tiering:** Not every agent needs the same model. The Classifier and Responder do simple structured output tasks — a cheaper or smaller model handles them fine. The HypothesisAgent is doing the hardest reasoning and benefits most from a better model. Route each agent to the model that fits its task.

#### 🔵 Advanced

**Optimization 1 — Classifier Caching**

`ClassificationResult` is a pure function of `(service_name, error_type)` — the same incident fingerprint always maps to the same incident type and tool set. Caching with a 1-hour TTL in Redis is safe: incident patterns don't change within an hour, and if the classification does change (e.g., a new runbook was added for a DB incident type), the TTL ensures eventual consistency. The cache key is deliberately coarse — `service_name:error_type` not `service_name:error_type:title` — because minor title variations of the same incident type should hit the same cache entry.

```python
# backend/agents/agent_orchestrator.py
# Add this before the classifier.run() call in AgentOrchestrator.run()

CLASSIFICATION_CACHE_TTL = 3600  # 1 hour

async def _get_or_classify(self, memory: AgentMemory) -> AgentMemory:
    """
    Optimization 1: Cache classification results by incident pattern.
    Same service + error_type always maps to the same tool set.
    Skips the LLM call entirely on repeat incident patterns.
    """
    cache_key = (
        f"sentinel:classification:"
        f"{memory.alert.service_name}:{memory.alert.error_type}"
    )
    cached = await self.redis.get(cache_key)

    if cached:
        memory.classification = ClassificationResult.model_validate_json(cached)
        logger.info(
            f"[Orchestrator] Classification cache HIT for "
            f"{memory.alert.service_name}:{memory.alert.error_type}"
        )
        return memory

    # Cache miss — run the LLM classifier
    memory = await self.classifier.run(memory)

    # Cache the result for future identical incident patterns
    await self.redis.setex(
        cache_key,
        CLASSIFICATION_CACHE_TTL,
        memory.classification.model_dump_json(),
    )
    logger.info(
        f"[Orchestrator] Classification cached for "
        f"{memory.alert.service_name}:{memory.alert.error_type}"
    )
    return memory
```

Update `AgentOrchestrator.run()` to call `_get_or_classify` instead of `self.classifier.run`:

```python
# Before (line in run()):
memory = await self.classifier.run(memory)

# After:
memory = await self._get_or_classify(memory)
```

**Interview story:** _"I cache classification results by `(service_name, error_type)` in Redis with a 1-hour TTL. A DB timeout on payments-api always maps to the same tool set — there's no reason to pay for an LLM call to re-derive that. Cache hits skip the Classifier entirely, saving ~480 tokens and ~500ms per repeated incident pattern."_

---

**Optimization 2 — Message History Pruning**

The ReAct loop accumulates a growing message list: system prompt + initial user message + N rounds of `(assistant thought-action, user observation)`. By step 4, this can be 2,000–3,000 tokens, most of which is old observations the agent has already acted on. Pruning after step 3 keeps the context window tight without losing continuity — the agent retains a summary of what it tried and the last two exchanges in full.

```python
# backend/agents/investigator_agent.py
# Add this inside the ReAct loop, at the start of each step after step 3

MAX_HISTORY_STEPS_BEFORE_PRUNE = 3

# In the for loop, before the LLM call:
if step_num > MAX_HISTORY_STEPS_BEFORE_PRUNE and len(messages) > 8:
    # Summarize what the agent has tried so far in one line
    tried_tools = [s.action for s in memory.agent_steps]
    key_findings = [s.observation[:100] for s in memory.agent_steps[-2:]]
    summary_content = (
        f"[Context summary — tools already called: {tried_tools}. "
        f"Most recent findings: {key_findings}. Continue investigating.]"
    )
    # Keep: system prompt (messages[0]) + summary + last 2 full exchanges (4 messages)
    messages = (
        [messages[0]]                    # system prompt — always keep
        + [{"role": "user", "content": summary_content}]  # compressed history
        + messages[-4:]                  # last 2 full thought-action-observation pairs
    )
    logger.info(
        f"[InvestigatorAgent] Pruned message history at step {step_num}. "
        f"Kept system prompt + summary + last 2 exchanges."
    )
```

**Why last 2 exchanges and not 1?** The LLM needs to see its most recent thought AND the observation that followed it to reason coherently about what to do next. Keeping only the last observation loses the reasoning context. Two full exchanges (4 messages) is the minimum for coherent continuation.

**Tradeoff:** The agent loses verbatim access to early observations after pruning. In practice this is fine — the validated evidence list in `memory.evidence` preserves what matters, and the summary line tells the agent what it has already tried so it doesn't repeat tool calls.

---

**Optimization 3 — Model Tiering**

Not all agents need the same reasoning depth. Routing each agent to the right model size reduces cost without sacrificing quality where it matters.

```python
# backend/services/llm_service.py
# LLMService already takes a model parameter. Wire different models per agent.

# backend/agents/agent_orchestrator.py — in __init__ or in the worker wiring:

from ..config import settings

# Cheap model for simple structured output tasks
cheap_llm = LLMService(api_key=settings.openai_api_key, model="gpt-4o-mini")

# Better model only for the hardest reasoning task
hypothesis_llm = LLMService(api_key=settings.openai_api_key, model="gpt-4o-mini")
# Swap to "gpt-4o" only if hypothesis quality is genuinely insufficient:
# hypothesis_llm = LLMService(api_key=settings.openai_api_key, model="gpt-4o")

orchestrator = AgentOrchestrator(
    classifier=ClassifierAgent(cheap_llm),      # simple categorization
    investigator=InvestigatorAgent(cheap_llm, tool_registry),  # tool routing
    hypothesis=HypothesisAgent(hypothesis_llm), # hardest reasoning
    responder=ResponderAgent(cheap_llm),         # simple formatting
    incident_repo=incident_repo,
    redis=redis_client,
)
```

**Tiering rationale per agent:**

|Agent|Task complexity|Model choice|Why|
|---|---|---|---|
|Classifier|Low — fixed output schema, 5 possible categories|`gpt-4o-mini`|Reliable categorization, no deep reasoning needed|
|Investigator|Medium — tool selection from a short list|`gpt-4o-mini`|Tool names are constrained; the model just needs to follow the ReAct format|
|HypothesisAgent|High — causal reasoning over ambiguous evidence|`gpt-4o-mini` default, `gpt-4o` if quality is insufficient|This is where wrong answers hurt most|
|Responder|Low — reformatting hypothesis into commands|`gpt-4o-mini`|Pure formatting task; no novel reasoning required|

**Interview story:** _"I use model tiering — `gpt-4o-mini` for the Classifier and Responder (simple structured output), and the same or a better model only for the HypothesisAgent which does the hardest causal reasoning. The Classifier is just categorizing into 5 buckets; it doesn't need the same reasoning depth as the agent synthesizing a root cause from ambiguous evidence."_

---

**Combined token savings summary:**

|Optimization|Tokens saved|When it applies|
|---|---|---|
|Log summarization (built)|~1,800/investigation|Always|
|Runbook truncation (built)|~400–800/investigation|When RAG returns results|
|Classifier caching|~480/investigation|On repeat `(service, error_type)` patterns|
|Message history pruning|~800–1,500/investigation|When Investigator runs ≥4 steps|
|Model tiering|~20–30% cost reduction|Always (same token count, lower price/token)|

---

## 9. API Layer (FastAPI)

### 🟢 Simple

Same as before — three endpoints, same rate limiting. One addition: `GET /incidents/{id}/trace` returns the full agent trace, showing every thought-action-observation step.

### 🔵 Advanced

The `/trace` endpoint queries `agent_traces` (JSONB) and returns the full `AgentMemory` object. This is valuable for debugging and demos — showing the agent's reasoning process step-by-step is the most impressive part of the demo. The dashboard's `AgentTracePanel` component renders this as an expandable timeline.

```python
# backend/api/routers/incidents.py — add this endpoint
@router.get("/incidents/{incident_id}/trace")
async def get_agent_trace(incident_id: UUID, repo: IncidentRepository = Depends(get_incident_repo)):
    """Returns the full agent reasoning trace for an incident."""
    trace = await repo.get_agent_trace(incident_id)
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")
    return trace
```

All other API code is unchanged from the original version.

---

## 10. Database Schema

### 🟢 Simple

Same five tables plus one new one: `agent_traces`. This stores the full step-by-step reasoning for every investigation — every thought, every tool call, every observation. This is what shows up in the "Agent Trace" panel on the dashboard and makes the demo impressive.

### 🔵 Advanced

`agent_traces` stores `AgentMemory` as a JSONB column — the entire investigation history in one row. JSONB enables querying specific fields (`memory->'investigator_confidence'`) without full deserialization. The `agent_steps` within the JSONB are also individually queryable: `memory->'agent_steps'->0->'action'` retrieves the first tool call. This is correct for a portfolio project; a production system would normalize the steps into a separate table for efficient querying.

```sql
-- All original tables unchanged. Add this one:

CREATE TABLE agent_traces (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    incident_id UUID NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    memory JSONB NOT NULL DEFAULT '{}',      -- full AgentMemory serialized
    incident_type VARCHAR(50),               -- from ClassificationResult
    investigator_steps INTEGER DEFAULT 0,    -- how many ReAct steps
    total_llm_calls INTEGER DEFAULT 0,
    investigator_confidence FLOAT,
    hypothesis_confidence FLOAT,
    escalated BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT one_trace_per_incident UNIQUE (incident_id)
);
CREATE INDEX idx_traces_incident ON agent_traces(incident_id);
CREATE INDEX idx_traces_type ON agent_traces(incident_type);
```

---

## 11. Frontend Dashboard (Next.js)

### 🟢 Simple

Two pages: incident list and incident detail. Same 5-second polling. One new component: `AgentTracePanel` — this is the most impressive part of the demo. It shows the agent's reasoning step by step:

```
Step 1: THOUGHT: "DB incident. Start with logs."
        ACTION: search_logs("payments-api")
        OBSERVATION: "847 connection refused errors..."

Step 2: THOUGHT: "Connection errors spiked. Check deploys."
        ACTION: search_deploys("payments-api", hours=2)
        OBSERVATION: "Deploy at 01:27 AM changed pool_size: 20→5"

Step 3: THOUGHT: "Strong signal. Verify with runbook."
        ACTION: search_runbooks("connection pool exhaustion")
        OBSERVATION: "pool_size < concurrent_requests causes timeout..."

Step 4: THOUGHT: "Confidence > 0.85. Stop."
        ACTION: finish(confidence=0.91)
```

Watching this panel populate in real time — seeing the agent _think_ — is what makes interviewers remember your demo.

### 🔵 Advanced

`AgentTracePanel` polls `GET /incidents/{id}/trace` every 2 seconds while the investigation is in progress (status == "investigating"), switching to static display once complete. Each step is rendered as a timeline entry with color-coded action types (tool calls in blue, finish in green). The panel also shows per-agent timing and total LLM calls — this gives reviewers a concrete sense of the system's behavior and cost.

```tsx
// frontend/components/AgentTracePanel.tsx
import { useEffect, useState } from "react";

interface AgentStep {
  step_number: number;
  thought: string;
  action: string;
  action_input: Record<string, any>;
  observation: string;
  timestamp: string;
}

interface AgentTrace {
  agent_steps: AgentStep[];
  classification: { incident_type: string; reasoning: string } | null;
  hypothesis: { root_cause: string; confidence: number; causal_chain: string[] } | null;
  response: { summary: string; immediate_actions: string[]; escalate: boolean } | null;
  total_llm_calls: number;
  investigator_confidence: number;
}

export default function AgentTracePanel({ incidentId, status }: { incidentId: string; status: string }) {
  const [trace, setTrace] = useState<AgentTrace | null>(null);

  useEffect(() => {
    const fetchTrace = async () => {
      const res = await fetch(`/api/proxy/incidents/${incidentId}/trace`);
      if (res.ok) setTrace(await res.json());
    };
    fetchTrace();
    if (status === "investigating") {
      const interval = setInterval(fetchTrace, 2000);
      return () => clearInterval(interval);
    }
  }, [incidentId, status]);

  if (!trace) return <div className="text-gray-400 text-sm">Waiting for agent...</div>;

  return (
    <div className="space-y-4">
      {/* Classification */}
      {trace.classification && (
        <div className="bg-blue-50 border border-blue-200 rounded-lg p-3">
          <p className="text-xs font-semibold text-blue-700 uppercase mb-1">Classifier</p>
          <p className="text-sm text-blue-900">
            Incident type: <strong>{trace.classification.incident_type}</strong>
          </p>
          <p className="text-xs text-blue-700 mt-1">{trace.classification.reasoning}</p>
        </div>
      )}

      {/* Investigator Steps */}
      {trace.agent_steps.length > 0 && (
        <div>
          <p className="text-xs font-semibold text-gray-500 uppercase mb-2">
            Investigator — {trace.agent_steps.length} steps
          </p>
          <div className="space-y-2">
            {trace.agent_steps.map((step) => (
              <div key={step.step_number} className="border border-gray-100 rounded-lg p-3 text-xs">
                <div className="flex items-center gap-2 mb-1">
                  <span className="w-5 h-5 bg-gray-100 rounded-full flex items-center justify-center text-gray-600 font-mono">
                    {step.step_number}
                  </span>
                  <span className={`px-2 py-0.5 rounded font-mono text-xs ${
                    step.action === "finish"
                      ? "bg-green-100 text-green-700"
                      : "bg-blue-100 text-blue-700"
                  }`}>
                    {step.action}
                  </span>
                </div>
                <p className="text-gray-500 mb-1"><span className="font-medium text-gray-700">Thought:</span> {step.thought}</p>
                <p className="text-gray-400 font-mono text-xs bg-gray-50 rounded p-2 mt-1 line-clamp-3">
                  {step.observation}
                </p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Hypothesis */}
      {trace.hypothesis && (
        <div className="bg-amber-50 border border-amber-200 rounded-lg p-3">
          <p className="text-xs font-semibold text-amber-700 uppercase mb-1">
            Hypothesis — {Math.round(trace.hypothesis.confidence * 100)}% confidence
          </p>
          <p className="text-sm text-amber-900">{trace.hypothesis.root_cause}</p>
          <ul className="mt-2 space-y-1">
            {trace.hypothesis.causal_chain.map((step, i) => (
              <li key={i} className="text-xs text-amber-700 flex gap-1">
                <span>→</span><span>{step}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Footer */}
      <div className="text-xs text-gray-400 flex gap-4">
        <span>LLM calls: {trace.total_llm_calls}</span>
        <span>Investigator confidence: {Math.round(trace.investigator_confidence * 100)}%</span>
      </div>
    </div>
  );
}
```

---

## 12. Docker Setup

### 🟢 Simple

Unchanged — one command starts everything. The agents run inside the same worker container. No new containers needed.

### 🔵 Advanced

The agent system runs entirely within the existing worker container — no new services required. At scale, you would extract the worker into a dedicated agent-worker service with auto-scaling, but for the portfolio project the existing Compose setup is correct. The `AgentMemory` checkpoint in Redis means multiple worker replicas are safe — each incident is locked to one worker via the distributed lock, and the checkpoint ensures no work is lost on worker restart.

All Docker Compose and Makefile code is unchanged from the original version.

---

## 13. The Demo Scenario (This Gets You Hired)

### 🟢 Simple

Same setup as before: deploy that changed `db_pool_size` from 20 to 5, 847 error logs saying "connection refused."

**You run the same curl command.** But what happens in the dashboard is completely different:

**Instead of a black box "investigating..." → answer, you watch the agent think:**

```
[Classifier]  → "DB incident. Use: logs, deploys, runbooks."

[Investigator] Step 1 → search_logs → "847 connection refused errors"
               Step 2 → search_deploys → "pool_size: 20→5 at 01:27 AM"
               Step 3 → search_runbooks → "pool exhaustion causes timeout"
               Step 4 → finish (confidence: 0.91)

[Hypothesis]  → "Deploy reduced pool from 20→5, exhausted under 18 req/s load"
               Causal chain: deploy → pool reduced → pool exhausted → errors spike

[Responder]   → kubectl rollout undo deployment/payments-api
```

**That's what makes it different.** Watching the agent _reason_ — seeing it decide what to look at next, reading what it found, watching it decide to stop — that's the demo no one else has.

**Total time: ~8 seconds. Alert in → 4-agent pipeline → root cause out.**

### 🔵 Advanced

The demo now exhibits two technically impressive properties:

**1. Adaptive investigation:** The Investigator calls `search_deploys` _because_ `search_logs` revealed a connection spike — not because it was predetermined. A different incident type (memory OOM) would have produced a different tool call sequence. This is genuine agentic behavior, not a fixed pipeline wearing an agent costume.

**2. Causal chain reconstruction across agents:** The Classifier identifies the incident type, the Investigator gathers evidence, the Hypothesis agent reconstructs the causal chain (`deploy → config change → resource exhaustion → error cascade`), and the Responder converts that chain into terse commands. Each agent contributes one piece. No single agent could do all four jobs as well.

```
What Sentinel does internally (8 seconds):

[Classifier — 500ms, 1 LLM call]
  → incident_type: "database"
  → suggested_tools: ["search_logs", "search_deploys", "search_runbooks"]
  → skip: ["check_kubernetes_pods", "check_related_services"]

[Investigator — 5s, 4 LLM calls]
  Step 1: search_logs("payments-api", window=30)
          → "847 connection refused, peak at 02:17 AM"
  Step 2: search_deploys("payments-api", hours=2)
          → "v2.4.1 at 01:27 AM, diff: pool_size 20→5"
  Step 3: search_runbooks("DB connection pool exhaustion timeout cascade")
          → "pool_size < concurrent_requests → queue buildup → timeout"
  Step 4: finish(confidence=0.91, evidence=[...])

[Hypothesis — 1s, 1 LLM call]
  → root_cause: "v2.4.1 deploy reduced connection pool from 20→5..."
  → causal_chain: [deploy, pool reduction, exhaustion at 18 req/s, error cascade]
  → alternative: "DB server overload" → ruled_out_by: "no CPU metric spike"

[Responder — 1s, 1 LLM call]
  → kubectl rollout undo deployment/payments-api
  → escalate: false

Total: 7 LLM calls, ~$0.04, 8 seconds end-to-end
```

---

## 14. The 3-Week Build Plan

### 🟢 Simple

**Week 1 — Build the foundation (same as before):**

|Day|Task|What you learn|
|---|---|---|
|1|Docker Compose + Postgres + Redis + Pydantic config|Project setup, env management|
|2|SQLAlchemy models + Alembic migration + IncidentRepository|ORM, schema design|
|3|FastAPI alert endpoint + AlertService with dedup|Async API, idempotency|
|4|LogService + DeployService with mock data|Service design, asyncio.gather|
|5|FAISS index builder + RAGService|Embeddings, vector search|
|6|LLMService + circuit breaker + fallback|Failure handling|
|7|Wire original InvestigationService — get demo working first|End-to-end validation|

**Goal Day 7:** `curl -X POST /alerts` → result in DB. Demo works. Now upgrade it.

**Week 2 — Add the agent layer:**

|Day|Task|What you learn|
|---|---|---|
|8|`BaseTool`, `LogTool`, `DeployTool`, `RunbookTool`, `ToolRegistry`|Tool abstraction, dispatch table|
|9|`BaseAgent`, `AgentMemory`, `ClassifierAgent`|Template Method, Blackboard pattern|
|10|`InvestigatorAgent` — ReAct loop with real tools|ReAct pattern, agentic reasoning|
|11|`HypothesisAgent` + `ResponderAgent`|Specialized reasoning per agent|
|12|`AgentOrchestrator` — wire all four agents|Pipeline coordination, checkpointing|
|13|Replace worker's `InvestigationService` with `AgentOrchestrator`|System integration|
|14|Full end-to-end demo with agent trace|End-to-end agentic flow|

**Week 3 — Make it impressive:**

|Day|Task|What you learn|
|---|---|---|
|15|BM25 → hybrid retrieval|Hybrid search|
|16|Next.js dashboard + `AgentTracePanel`|Frontend, polling|
|17|Agent trace DB schema + `/trace` endpoint|JSONB storage, API|
|18|Simulate Incident button + seeded demo|Demo polish|
|19|RAG accuracy eval + grounding eval suite + classifier caching + message history pruning + model tiering|Evaluation, hallucination defense, token optimization|
|20|Unit tests: ClassifierAgent, InvestigatorAgent (mock tools)|Testing agentic code|
|21|Record Loom demo, polish README, push to GitHub|Presentation|

### 🔵 Advanced

**Why build the pipeline version first (Week 1, Day 7)?**

The agent system builds _on top of_ the working pipeline. The tools call the same services. The agents use the same LLMService. If you start with agents, you're debugging agent behavior and service behavior simultaneously — a combinatorial debugging problem. Build the pipeline, validate the services work, then add agents on Day 8. This is how production teams ship agentic systems: service layer first, agents on top.

**The Day 13 integration is one file change in the worker** — everything else is additive. This is the payoff of keeping agents in their own layer: `InvestigationService` and `AgentOrchestrator` are interchangeable from the worker's perspective.

**Defense 6 — Grounding eval suite (Day 19):** This is your long-term defense against systematic hallucination. It's not a code check — it's measurement. For each test case you seed the DB with known data (specific deploys, specific logs), run the full agent pipeline, and assert two things: (1) forbidden evidence terms never appear in results (the agent didn't invent things that weren't there), and (2) expected evidence terms always appear (the agent didn't miss obvious signals). This catches regressions when you change prompts, swap embedding models, or upgrade the LLM.

```python
# tests/eval/test_agent_grounding.py
GROUNDING_TEST_CASES = [
    {
        "alert": {
            "service_name": "payments-api",
            "error_type": "db_timeout",
            "severity": "P1",
            "title": "DB connection timeout",
            "description": "Error rate exceeded 5%",
        },
        "seeded_deploys": [{"version": "v2.4.1", "diff_summary": "pool_size: 20→5"}],
        "seeded_logs": [{"message": "connection refused", "level": "ERROR"}] * 847,
        # These terms MUST appear in evidence — they're in the seeded data
        "expected_evidence_contains": ["pool_size", "connection refused"],
        # These terms must NOT appear — they were never in the seeded data
        "forbidden_evidence": ["cpu", "memory", "kubernetes", "jvm", "oom"],
        # Root cause must mention the actual cause
        "expected_root_cause_contains": ["pool"],
    },
]

def test_agent_does_not_hallucinate_evidence():
    for case in GROUNDING_TEST_CASES:
        result = run_agent_pipeline_with_seeded_data(case)
        evidence_text = " ".join(result.evidence).lower()

        for forbidden in case["forbidden_evidence"]:
            assert forbidden not in evidence_text, (
                f"Agent hallucinated '{forbidden}' in evidence — "
                f"this term was never in the seeded data"
            )
        for required in case["expected_evidence_contains"]:
            assert required in evidence_text, (
                f"Agent missed expected evidence '{required}' — "
                f"this was clearly in the seeded data"
            )
        assert any(
            term in result.hypothesis.root_cause.lower()
            for term in case["expected_root_cause_contains"]
        ), f"Root cause doesn't mention expected term: {case['expected_root_cause_contains']}"
```

This is also how you get the "agent-composed queries outperformed fixed queries by 18%" resume metric — run this eval with and without the grounding check enabled, compare the accuracy numbers.

---

## 15. Design Patterns You'll Use (Interview Gold)

### 🟢 Simple

You don't need to memorize these. You'll build them, then recognize what they're called.

|Pattern|Where in Sentinel|What to say in interviews|
|---|---|---|
|**ReAct**|`InvestigatorAgent` — think → act → observe loop|"The agent reasons about what tool to call, calls it, observes the result, then decides what to do next"|
|**Blackboard**|`AgentMemory` shared by all agents|"Agents communicate through shared state — no direct coupling between agents"|
|**Template Method**|`BaseAgent.run()` — common lifecycle, custom steps|"Every agent gets circuit breaker and logging for free by inheriting run()"|
|**Dispatch Table**|`ToolRegistry` — string name → tool instance|"The LLM outputs a string; the registry routes it to the right implementation"|
|**Strategy**|`BaseTool` → `LogTool`, `DeployTool`, `RunbookTool`|"I can add a new tool without changing the agent"|
|**Repository**|`IncidentRepository` wraps all Postgres queries|"If I switch databases, only one file changes"|
|**Circuit Breaker**|LLMService stops after 3 failures|"System degrades gracefully instead of hanging"|
|**Dependency Injection**|FastAPI `Depends()` + agent constructors|"Every dependency is injected — nothing is hardcoded"|
|**Idempotency**|Redis SETNX + Postgres UPSERT|"Safe to retry — twice = same result as once"|
|**Grounding Check**|`_validate_evidence()` + `_validate_hypothesis()`|"Evidence the agent claims must be traceable to a real tool observation"|
|**Fail-Safe Escalation**|Confidence < 0.6 → escalate regardless of agent output|"The worst case is always a human gets paged, never a wrong fix executed silently"|
|**Cache-Aside**|Classifier caching in Redis by `(service_name, error_type)`|"Pure function of input — same incident type always maps to same tool set, so I skip the LLM call on repeat patterns"|
|**Context Window Management**|Message history pruning after step 3 in ReAct loop|"Old observations the agent already acted on are compressed to a summary — keeps context tight without losing continuity"|
|**Model Tiering**|Different LLM per agent based on task complexity|"Simple formatting tasks use the cheaper model; hard causal reasoning gets the better model"|

### 🔵 Advanced

**ReAct Pattern (Reason + Act)**

The ReAct pattern (Yao et al., 2022) interleaves reasoning traces with actions. The LLM outputs `{thought, action, action_input}` → the system executes the action → the observation is appended to the message history → the LLM reasons about the next action with full context of what it has tried. The key property: the LLM's context accumulates the full thought-action-observation history, giving it effective memory across steps without explicit memory management.

**Blackboard Pattern**

The Blackboard pattern (Nii, 1986) is a coordination mechanism where specialist agents communicate through a shared knowledge store rather than direct messages. In Sentinel: `AgentOrchestrator` sequences agents; agents read from and write to `AgentMemory`; no agent imports or calls another. Adding a fifth agent requires: (1) implement `BaseAgent`, (2) add one line to `AgentOrchestrator`. The blackboard stores the full interaction history, enabling both coordination and audit.

**Template Method for Agent Lifecycle**

`BaseAgent.run()` is the invariant algorithm; `_build_prompt()` and `_parse_output()` are the variant steps. Every concrete agent gets: circuit breaker protection (inherited from `LLMService`), token counting (`memory.total_llm_calls += 1`), latency logging. The `InvestigatorAgent` overrides `run()` because it has a loop, but inherits `_build_prompt()` for per-step prompt construction.

**Dispatch Table over Switch Statement**

The alternative to `ToolRegistry` is a switch statement: `if action == "search_logs": LogTool().run(...)`. This violates Open/Closed Principle — adding a new tool requires modifying the switch. `ToolRegistry` is a dict: adding a new tool is `registry["new_tool"] = NewTool()`. The LLM's string output maps directly to the registry key — a clean, extensible dispatch mechanism.

---

## 16. Interview Prep — Questions This Project Answers

### "What is agentic AI and how did you implement it?"

**🟢 Simple:**

> "Agentic AI means the AI decides what to do next, rather than following a fixed script. In my project, the Investigator Agent runs a loop: it looks at the alert, decides which tool to call, calls it, reads what it got back, and then decides whether to call another tool or stop. It's like giving the AI a set of tools and letting it figure out which ones it needs — instead of me hardcoding 'always check logs, then deploys, then runbooks.'"

**🔵 Advanced:**

> "I implemented the ReAct pattern (Yao et al., 2022): at each step, the LLM receives the accumulated thought-action-observation history and outputs `{thought, action, action_input}`. A `ToolRegistry` dispatches the action to the correct tool implementation. The observation is appended to the message history, giving the LLM effective context memory. The loop terminates when the LLM outputs `action: finish` with a confidence score, or when `MAX_STEPS = 6` is hit — the hard guardrail that prevents infinite loops. This is the same pattern used in production at Cognition (Devin) and Uber's Genie system."

---

### "Why four agents instead of one?"

**🟢 Simple:**

> "Each agent has one job and a smaller context window. If one agent is doing everything — deciding what tools to call, reading all the tool outputs, forming a hypothesis, AND writing the response — its context gets cluttered and its output quality drops. A Hypothesis Agent that only sees clean evidence produces better causal reasoning than one that also has 2,000 tokens of raw log output in its context."

**🔵 Advanced:**

> "Context window pollution is the core problem. A single agent accumulates: alert + classification thoughts + all tool outputs (logs, deploy diffs, runbook chunks) + hypothesis reasoning + response draft. By step 6, the LLM is reasoning about its final answer while distracted by thousands of tokens of raw log output from step 1. The multi-agent split isolates concerns: Investigator's context = tool history; Hypothesis Agent's context = clean evidence list; Responder's context = hypothesis only. This matches the empirical findings from the CAMEL and AutoGen papers — specialized agents with focused prompts outperform single generalist agents on decomposable tasks."

---

### "How do you handle failures in the agent pipeline?"

**🟢 Simple:**

> "Three layers. If an individual tool call fails, the agent gets the error as its observation and can try a different tool. If the LLM API fails, the circuit breaker in LLMService kicks in and returns a fallback response. If the entire agent pipeline fails, the `AgentOrchestrator` catches the exception, marks the incident as failed, and the checkpoint in Redis means a restarted worker can resume from the last completed agent rather than starting over."

**🔵 Advanced:**

> "Failure handling operates at three granularities: (1) **Tool level** — `ToolRegistry.execute()` catches exceptions and returns the error string as the observation. The agent observes the failure and can choose a different tool on the next step. (2) **Agent level** — the `LLMService` circuit breaker prevents cascading failures when the LLM API is down. `BaseAgent.run()` catches exceptions and propagates them to the orchestrator. (3) **Pipeline level** — `AgentOrchestrator` checkpoints `AgentMemory` to Redis after each agent. On worker restart, the orchestrator reads the checkpoint and resumes at the next uncompleted agent — implementing the **saga pattern**: each agent is a compensatable transaction."

---

### "How do you prevent the agent from looping forever?"

**🟢 Simple:**

> "Hard limit of 6 steps. If the agent hasn't called `finish` by step 6, the orchestrator stops it, sets confidence to 0, and escalates to a human. The agent can never override this — `MAX_STEPS` is checked in Python, not in the prompt."

**🔵 Advanced:**

> "`MAX_STEPS = 6` is a **capability restriction** enforced in the Python `run()` loop — not in the prompt. Prompts can be ignored or misinterpreted by LLMs; Python `for step_num in range(1, MAX_STEPS + 1)` cannot. The termination condition `action == finish` is evaluated in Python, not by the LLM. When `MAX_STEPS` is hit, the orchestrator sets `investigator_confidence = 0.0` and `escalate = True` — converting a runaway agent into a safe, deterministic escalation. This is the **fail-safe** principle applied to agent design: the worst case is always a human getting paged, never an infinite loop."

---

### "Why hand-roll the ReAct loop instead of using LangChain?"

**🟢 Simple:**

> "I wanted to understand every line. LangChain abstracts the tool dispatch, observation formatting, and memory management — you lose the ability to explain how it works. My ReAct loop is 150 lines of Python I can walk through completely in an interview. The tradeoff: more code to write, but complete understanding."

**🔵 Advanced:**

> "LangChain's `AgentExecutor` abstracts: (1) the message history accumulation, (2) tool dispatch via `ToolSpec` schemas, (3) output parsing with `OutputParser`, (4) the termination condition. All of these have non-obvious behaviors when things go wrong — debugging a LangChain agent requires understanding the abstraction's internals anyway. By hand-rolling, I explicitly control: the message format the LLM receives at each step, how observations are formatted and appended, how `finish` is detected, and what happens on `MAX_STEPS`. Each line is deliberately designed. In a portfolio project, this ownership is the point — it demonstrates depth, not just familiarity with a framework API."

---

### "How do you prevent hallucinations in your LLM output?"

**🟢 Simple:**

> "Six layers, each at a different point in the pipeline. When the Investigator calls `finish`, I cross-check every evidence point it claims against what the tools actually returned — invented evidence gets dropped. If it calls a tool that doesn't exist, it gets an error message as its observation and can recover instead of crashing. After the Investigator finishes, I check whether its confidence is believable — high confidence from a single tool call gets capped. The HypothesisAgent's root cause is checked for overlap with the actual evidence — if it's making up a causal chain, confidence is set below the escalation threshold. Every agent's output is validated against a Pydantic schema before entering shared memory. And there's a grounding eval test suite that runs assertions on what terms must and must not appear in results for known inputs."

**🔵 Advanced:**

> "Six defenses layered across the pipeline. (1) **Evidence grounding** — `_validate_evidence()` cross-checks each `finish` claim against `memory.tool_calls` observations using term overlap. Claims with fewer than 2 meaningful term matches are dropped; confidence is penalized by `grounding_ratio = len(validated) / len(raw)`. (2) **Tool name validation** — `ToolNotFoundError` is caught and converted to a corrective observation string, letting the agent recover rather than crashing; two hallucinated tool names deplete steps → `MAX_STEPS` → escalation. (3) **Confidence calibration** — the `AgentOrchestrator` caps confidence at 0.5 when `tool_call_count < 2 AND confidence > 0.7`, catching the LLM's tendency to be overconfident on thin evidence. (4) **Hypothesis grounding** — `_validate_hypothesis()` checks term overlap between the root cause and `memory.evidence`; fewer than 3 meaningful matches caps confidence at 0.4, which flows through to `escalate: True` in the Responder. (5) **Schema enforcement** — every agent's `_parse_output()` is Pydantic-validated in `BaseAgent.run()`; a `ValidationError` raises `AgentOutputError` immediately rather than propagating corrupted data. (6) **Grounding eval suite** — `test_agent_grounding.py` runs on every PR with seeded DB data, asserting forbidden terms never appear in results and expected terms always do; this catches systematic hallucination regressions across prompt or model changes."

---

### "What would you change at 10x scale?"

**🟢 Simple:**

> "Replace Redis queue with Kafka for replay. Run each agent in its own process pool for parallel investigations. Add OpenTelemetry tracing so you can follow one investigation across all four agents. Classifier caching is already implemented — at scale I'd extend it with a smarter invalidation strategy tied to runbook updates."

**🔵 Advanced:**

> "At 10K+ alerts/day: (1) Replace Redis `LPUSH`/`BLPOP` with Kafka — partition-level parallelism, offset-based replay for failed investigations. (2) Extract the agent worker into a dedicated auto-scaling service — agent pipelines are CPU/network bound, not the same profile as the API. (3) Add OpenTelemetry distributed tracing — propagate `trace_id` from alert ingestion through every agent step and tool call. (4) Classifier caching is already in place (Redis, 1hr TTL by `service_name:error_type`) — at scale, add cache invalidation on runbook index updates so a new runbook triggers re-classification of its incident type. (5) Implement RAG eval in CI — any embedding model change runs the 20-query eval set, fails if accuracy drops >5%. (6) Replace FAISS with Qdrant for native metadata filtering in `RunbookTool` — filter by service-specific runbooks without the ×1.2 boost hack. (7) Extend message history pruning to use a dedicated summarizer LLM call instead of a hand-written summary string — more expensive but produces better context compression for long investigations."

---

## 17. The Resume Bullet

```
Sentinel — Multi-Agent Incident Investigation System
• Designed a 4-agent autonomous incident investigation system (Classifier →
  Investigator → Hypothesis → Responder) using a hand-rolled ReAct loop,
  reducing simulated triage time from ~30 minutes to under 10 seconds
• Implemented InvestigatorAgent with adaptive tool selection (LogTool, DeployTool,
  RunbookTool) via ToolRegistry dispatch — agent dynamically decides which evidence
  to gather based on observed results, not a fixed pipeline
• Built 6-layer hallucination defense system: evidence grounding cross-check,
  tool name validation with corrective observations, confidence calibration,
  hypothesis grounding, Pydantic schema enforcement, and a grounding eval suite
  asserting forbidden terms never appear in results for known seeded inputs
• Reduced per-investigation token cost by ~40% through classifier result caching
  (Redis, 1hr TTL by incident fingerprint), ReAct message history pruning after
  step 3, and model tiering (gpt-4o-mini for Classifier/Responder, stronger model
  reserved for HypothesisAgent causal reasoning)
• Built hybrid RAG retrieval (FAISS dense + BM25 sparse) called by RunbookTool,
  achieving 76% root cause accuracy on a 20-incident evaluation set; agent-composed
  queries outperformed fixed alert-description queries by 18%
• Engineered async agent pipeline (FastAPI + Redis queue + PostgreSQL) with
  AgentMemory blackboard pattern, checkpoint-based crash recovery, and
  MAX_STEPS=6 hard guardrail preventing infinite loops
• Stack: Python · FastAPI · PostgreSQL · Redis · FAISS · sentence-transformers ·
         OpenAI API (GPT-4o-mini) · Next.js · Docker
```

**What changed from the non-agentic bullet:**

- "4-agent autonomous investigation system" — names the architecture explicitly
- "hand-rolled ReAct loop" — signals depth, not framework usage
- "dynamically decides which evidence to gather" — describes what makes it agentic
- "agent-composed queries outperformed fixed queries by 18%" — a metric only possible with the agentic version
- "AgentMemory blackboard pattern" — named design pattern for the inter-agent communication

---

## 18. Reference Links — Everything You Need

### Official Docs

|Tool|Link|
|---|---|
|FastAPI|fastapi.tiangolo.com|
|SQLAlchemy 2.0 async|docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html|
|Alembic|alembic.sqlalchemy.org/en/latest/tutorial.html|
|Pydantic v2|docs.pydantic.dev/latest|
|Redis async client|redis-py.readthedocs.io/en/stable/examples/asyncio_examples.html|
|FAISS|faiss.ai|
|sentence-transformers|sbert.net/docs/quickstart.html|
|rank-bm25|github.com/dorianbrown/rank_bm25|
|OpenAI API|platform.openai.com/docs|
|Next.js App Router|nextjs.org/docs/app|
|Docker Compose|docs.docker.com/compose|
|tenacity|tenacity.readthedocs.io|

### Agentic AI (new — read these)

|Resource|What it teaches|
|---|---|
|ReAct paper (arxiv.org/abs/2210.03629)|The original ReAct pattern — how interleaved reasoning + action works|
|Uber Genie blog (uber.com/blog/genie-ubers-gen-ai-on-call-copilot)|RAG + agentic investigation for on-call engineers — your exact use case|
|Uber Enhanced Agentic RAG (uber.com/blog/enhanced-agentic-rag)|Query rewriting agents, hybrid retrieval at Uber scale|
|AutoGen paper (microsoft.com/en-us/research/project/autogen)|Multi-agent conversation patterns|
|Anthropic prompt engineering|docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/overview|

### Concepts (read in this order)

|Concept|Resource|
|---|---|
|Python async/await|realpython.com/async-io-python|
|Repository pattern|martinfowler.com/eaaCatalog/repository.html|
|Circuit breaker|martinfowler.com/bliki/CircuitBreaker.html|
|SOLID principles|realpython.com/solid-principles-python|
|Redis as a queue|redis.io/docs/manual/patterns/reliable-queue|
|Redis distributed locks|redis.io/docs/manual/patterns/distributed-locks|
|PostgreSQL indexing|use-the-index-luke.com|
|RAG paper (original)|arxiv.org/abs/2005.11401|
|Chunking strategies|pinecone.io/learn/chunking-strategies|
|BM25 explained|emschwartz.me/understanding-the-bm25-full-text-search-algorithm|
|Event-driven architecture|martinfowler.com/articles/201701-event-driven.html|
|System Design Primer|github.com/donnemartin/system-design-primer|
|Idempotency (Stripe)|stripe.com/blog/idempotency|
|CQRS pattern|learn.microsoft.com/en-us/azure/architecture/patterns/cqrs|
|Saga pattern|microservices.io/patterns/data/saga.html|
|Blackboard pattern|en.wikipedia.org/wiki/Blackboard_(design_pattern)|

---

## Quick Start

```bash
git clone https://github.com/yourusername/sentinel.git && cd sentinel
cp .env.example .env          # add your OPENAI_API_KEY
make dev                      # starts everything + seeds DB + builds FAISS index
make simulate                 # send a test P1 incident
# open http://localhost:3000  → watch all four agents reason in real time
```

---

## One Last Thing

### 🟢 Simple

Most people building projects like this get stuck trying to make everything perfect before the demo works. Don't do that.

**Build the pipeline version first.** Get the demo working on Day 7. _Then_ add agents on top of a working system. This is the only sane order.

The moment someone watching your demo sees the Agent Trace panel — watching the Investigator call `search_deploys` _because_ it found a log spike, not because it was hardcoded to — that's when they realize you built something genuinely agentic.

Build it. Know every design decision. Be able to explain the ReAct loop from first principles.

### 🔵 Advanced

The most common failure mode for agentic portfolio projects is **over-abstraction before validation** — installing LangChain, configuring tool schemas, wiring together an agent framework, and never getting to a working demo. The framework handles the hard parts, but you never understand what the hard parts are.

Invert the priority: (1) build the deterministic pipeline on Day 7 — this validates your services, your LLM prompts, and your data models, (2) add tools as thin adapters on Day 8 — one file per tool, (3) add the ReAct loop on Day 10 — 150 lines of Python you wrote yourself. Each step adds capability to a working system. No step requires throwing away previous work.

By Day 14 you have a demo where an interviewer can watch an AI agent reason through an incident in real time. That story — "I built a multi-agent system from scratch, I understand every line, and here's the ReAct loop I wrote" — is the one that gets callbacks from Cognition, Fireworks AI, and Decagon.

Know every design decision. Be able to defend every tradeoff. The agent traces tell the story themselves.

_$0–$20. 21 focused days. One story that no one else has._

---

_Built by Vishwas Patel | github.com/Vishwaspatel2401_