# Sentinel — AI-Powered Incident Investigation System

Sentinel is a multi-agent system that automatically investigates production incidents. When an alert fires, four AI agents classify the incident, gather evidence from logs and deploys, form a root cause hypothesis, and generate a step-by-step fix — all without human intervention.

Built as a learning project to deeply understand LLM engineering, distributed systems, OOP design patterns, and multi-agent architectures.

---

## What it does

```
Alert fires (Prometheus / Datadog)
        ↓
POST /api/v1/alerts  ← FastAPI receives it, saves to Postgres, pushes to Redis
        ↓
Redis queue  ← background worker picks it up with BLPOP
        ↓
┌─────────────────────────────────────────────────────┐
│                  Agent Pipeline                      │
│                                                      │
│  ClassifierAgent   → "db_issue"                      │
│        ↓                                             │
│  InvestigatorAgent → ReAct loop (reason + act)       │
│    • fetch_logs       → 847x "connection refused"    │
│    • fetch_deploys    → v2.4.1 changed pool_size     │
│    • search_runbooks  → pool exhaustion runbook      │
│        ↓                                             │
│  HypothesisAgent   → root cause + 95% confidence    │
│        ↓                                             │
│  ResponderAgent    → kubectl fix + escalation call   │
└─────────────────────────────────────────────────────┘
        ↓
Resolution saved to Postgres
        ↓
GET /api/v1/incidents/{id}  ← client polls for result
```

**Example output for the canonical demo scenario** (DB pool size reduced 20 → 5):

```json
{
  "status": "resolved",
  "resolution": {
    "root_cause": "Deployment v2.4.1 reduced pool_size from 20 to 5, causing connection exhaustion under normal load.",
    "confidence": 0.95,
    "suggested_fix": "1. kubectl rollout undo deployment/payments-api\n2. Monitor error rate for 5 min...",
    "evidence": [
      "v2.4.1 deployed 47 min before errors started",
      "847 connection refused errors in 30 minutes",
      "pool_size 5 insufficient for >18 req/s per runbook"
    ]
  }
}
```

---

## Architecture

### Week 1 — Linear Pipeline
```
Alert → LogService → DeployService → RAGService → LLMService → Resolution
```
One LLM call. Always fetches the same data in the same order. Simple and fast.

### Week 2 — Multi-Agent System
```
Alert → ClassifierAgent → InvestigatorAgent → HypothesisAgent → ResponderAgent → Resolution
                               ↕ tools
                    fetch_logs / fetch_deploys / search_runbooks
```
Four specialised agents. InvestigatorAgent uses a **ReAct loop** — it reasons about what to look at next, calls a tool, observes the result, and repeats until it has enough evidence. Both pipelines exist in the codebase; the worker uses Week 2 by default.

---

## Design patterns implemented

| Pattern | Where | What it does |
|---|---|---|
| **Template Method** | `BaseAgent` | Defines `think()` once; subclasses implement `system_prompt()` and `run()` |
| **ReAct loop** | `InvestigatorAgent` | Reason → Act → Observe → repeat until `DONE` |
| **Blackboard** | `context` dict | Shared memory between agents; each one reads what the previous wrote |
| **Strategy** | Tools | Each tool is an interchangeable strategy for gathering evidence |
| **Circuit Breaker** | `LLMService` | Opens after 3 failures; rule-based fallback runs instead of calling Claude |
| **Repository** | `IncidentRepository` | All DB logic for incidents in one class |
| **Facade** | `AgentOrchestrator` | Hides 4-agent complexity behind one `run(incident)` call |
| **Producer/Consumer** | FastAPI + Worker | FastAPI pushes to Redis; worker pops with `BLPOP` |
| **Composition** | Tools, Services | `FetchLogsTool` HAS-A `LogService`; delegates, doesn't inherit |

---

## Tech stack

| Layer | Technology |
|---|---|
| API | FastAPI + Uvicorn |
| Database | PostgreSQL + SQLAlchemy 2.0 (async) + Alembic |
| Queue | Redis (LPUSH / BLPOP) |
| LLM | Anthropic Claude (claude-haiku) |
| RAG | FAISS (vector search) + BM25 (keyword) — hybrid 60/40 |
| Embeddings | sentence-transformers/all-MiniLM-L6-v2 |
| Config | Pydantic Settings |
| Testing | pytest + pytest-asyncio + pytest-mock |
| Infrastructure | Docker Compose |

---

## Project structure

```
Sentinel/
├── backend/
│   ├── agents/
│   │   ├── base_agent.py            # abstract base — think(), blackboard context
│   │   ├── classifier_agent.py      # classifies incident type
│   │   ├── investigator_agent.py    # ReAct loop with tools
│   │   ├── hypothesis_agent.py      # root cause + confidence
│   │   └── responder_agent.py       # fix + escalation decision
│   ├── tools/
│   │   ├── base_tool.py             # abstract tool interface
│   │   ├── fetch_logs_tool.py       # queries log_entries table
│   │   ├── fetch_deploys_tool.py    # queries deploys table
│   │   └── runbook_tool.py          # FAISS + BM25 hybrid search
│   ├── services/
│   │   ├── agent_orchestrator.py    # wires all 4 agents, saves result
│   │   ├── investigation_service.py # Week 1 pipeline (single LLM call)
│   │   ├── llm_service.py           # Claude API + circuit breaker + fallback
│   │   ├── rag_service.py           # hybrid vector + keyword search
│   │   ├── log_service.py           # fetch + summarise recent errors
│   │   ├── deploy_service.py        # fetch recent deployments
│   │   └── alert_service.py         # parallel log + deploy gathering
│   ├── api/routers/
│   │   ├── alerts.py                # POST /api/v1/alerts
│   │   └── incidents.py             # GET /api/v1/incidents/{id}
│   ├── workers/
│   │   └── investigation_worker.py  # Redis consumer — runs agent pipeline
│   ├── db/
│   │   ├── models.py                # Incident, LogEntry, Deploy, Resolution
│   │   ├── database.py              # async engine + session factory
│   │   └── repositories/
│   │       └── incident_repo.py     # all DB queries for incidents
│   ├── schemas/
│   │   └── alert.py                 # Pydantic request/response models
│   └── config.py                    # type-safe settings from .env
├── tests/
│   ├── unit/                        # 48 tests — each class in isolation, all mocked
│   ├── integration/                 # 3 tests — real agents wired together
│   └── eval/                        # 8 tests — real LLM, run manually
├── data/
│   └── runbooks/                    # markdown runbooks indexed for RAG
├── scripts/
│   ├── seed_db.py                   # inserts demo incident + logs + deploy
│   └── build_index.py               # builds FAISS + BM25 index from runbooks
└── docker-compose.yml               # postgres + redis + backend + worker
```

---

## Running locally

### Prerequisites
- Python 3.11+
- Docker Desktop running

### 1. Clone and set up

```bash
git clone https://github.com/yourusername/Sentinel.git
cd Sentinel

python3 -m venv backend/venv
source backend/venv/bin/activate
pip install -r backend/requirements.txt
```

### 2. Configure environment

Create a `.env` file in the project root:

```
ANTHROPIC_API_KEY=sk-ant-...
LLM_MODEL=claude-haiku-4-5-20251001
DATABASE_URL=postgresql+asyncpg://sentinel:sentinel_dev@localhost/sentinel
REDIS_URL=redis://localhost:6379
API_SECRET_KEY=sentinel-dev-key
```

### 3. Start infrastructure

```bash
docker compose up -d postgres redis
```

### 4. Run database migrations

```bash
cd backend
alembic upgrade head
cd ..
```

### 5. Build the RAG index

```bash
python3 scripts/build_index.py
```

### 6. Seed demo data

```bash
python3 scripts/seed_db.py
```

Inserts: 1 incident, 847 "connection refused" log entries, 1 deploy (v2.4.1, `pool_size: 20→5`).

### 7. Start the worker and API

```bash
# Terminal 1
cd backend && python -m workers.investigation_worker

# Terminal 2
cd backend && uvicorn main:app --reload
```

### 8. Fire a test alert

```bash
curl -X POST http://localhost:8000/api/v1/alerts \
  -H "Content-Type: application/json" \
  -d '{
    "service_name": "payments-api",
    "severity": "P1",
    "title": "DB connection timeout",
    "description": "connection refused errors on /charge endpoint",
    "error_type": "db_timeout",
    "source": "prometheus"
  }'
```

### 9. Poll for the result

```bash
# Copy incident_id from the response above
curl http://localhost:8000/api/v1/incidents/<incident_id>
```

Status flips from `investigating` → `resolved` after ~15 seconds with the full analysis.

---

## Running tests

```bash
# Unit + integration — fast, no API calls, run on every change
backend/venv/bin/python -m pytest tests/unit/ tests/integration/ -v

# Eval — calls real Claude API, checks output quality, run manually
ANTHROPIC_API_KEY=sk-ant-... backend/venv/bin/python -m pytest tests/eval/ -v
```

| Suite | Tests | Speed | API calls |
|---|---|---|---|
| Unit | 48 | ~3s | None |
| Integration | 3 | ~3s | None |
| Eval | 8 | ~30s | Yes (costs money) |

---

## Key concepts demonstrated

**Abstraction** — `BaseAgent` hides LLM call details. Agents call `self.think()` without knowing which model, API, or how the circuit breaker works.

**Inheritance** — All agents inherit `think()`, `think_with_history()`, and `_strip_markdown()` from `BaseAgent` for free.

**Encapsulation** — `IncidentRepository` owns all DB logic for incidents. Nothing outside writes raw SQLAlchemy queries for incidents.

**Composition** — `FetchLogsTool` HAS-A `LogService`. `AgentOrchestrator` HAS-A all four agents. None of these inherit from each other — they delegate.

**Polymorphism** — `InvestigatorAgent` calls `tool.run(input)` on any `BaseTool` subclass without knowing the concrete type. Adding a new tool requires zero changes to the agent.

**Dependency Injection** — Sessions, services, and tools are passed into constructors, never created inside. This is why mocking in tests is straightforward — swap real objects for fakes without touching production code.

---

## What I learned building this

- **Async Python** — `asyncio.gather` for parallel I/O, `AsyncSession`, `BLPOP` blocking queue
- **SQLAlchemy 2.0** — `Mapped`, `mapped_column`, `selectinload` for eager loading relationships
- **RAG** — chunking with overlap, FAISS cosine similarity, BM25 keyword search, hybrid 60/40 scoring
- **ReAct agents** — multi-turn conversation history, tool call parsing, observation loops
- **Circuit breaker** — failure counting, open/half-open/closed states, rule-based fallback without LLM
- **Testing async code** — `AsyncMock`, `pytest-asyncio`, mocking at the right layer
- **Prompt engineering** — structured output formatting, why `temperature=0.1` matters for JSON responses
