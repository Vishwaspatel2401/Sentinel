# =============================================================================
# FILE: backend/core/metrics.py
# WHAT: Central registry of every Prometheus metric Sentinel exposes.
#       All counters, histograms and gauges are defined here — once.
# WHY:  If each module created its own metrics, you'd get duplicate-registration
#       errors when modules are imported more than once (common in FastAPI).
#       Defining them as module-level singletons here means they're created
#       exactly once, regardless of how many times the module is imported.
# HOW:  FastAPI exposes GET /metrics which calls generate_latest() to serialise
#       all registered metrics into Prometheus text format.
#       Prometheus scrapes /metrics every 15s and stores the time series.
#       Grafana queries Prometheus and renders the dashboard.
# METRICS:
#   sentinel_investigations_total        — counter, labels: status (resolved/failed)
#   sentinel_investigation_duration_seconds — histogram of pipeline wall time
#   sentinel_queue_depth                 — gauge, labels: queue (alert/dead)
#   sentinel_llm_calls_total             — counter, labels: status (success/failure)
#   sentinel_llm_call_duration_seconds   — histogram of single LLM call latency
#   sentinel_circuit_breaker_open        — gauge: 1 = open (degraded), 0 = closed (healthy)
#   sentinel_active_investigations       — gauge: jobs currently running
# =============================================================================

from prometheus_client import Counter, Histogram, Gauge

# ── Investigation pipeline ────────────────────────────────────────────────────

INVESTIGATIONS_TOTAL = Counter(
    "sentinel_investigations_total",
    "Total completed investigations, by final status",
    ["status"],          # labels: "resolved" | "failed"
)

INVESTIGATION_DURATION = Histogram(
    "sentinel_investigation_duration_seconds",
    "Wall-clock time from job pickup to completion (all 4 agents)",
    buckets=[5, 10, 15, 20, 30, 45, 60, 90, 120],
)

ACTIVE_INVESTIGATIONS = Gauge(
    "sentinel_active_investigations",
    "Number of investigations currently running across all workers",
)

# ── Queue ─────────────────────────────────────────────────────────────────────

QUEUE_DEPTH = Gauge(
    "sentinel_queue_depth",
    "Current number of items in a Redis queue",
    ["queue"],           # labels: "alert" | "dead"
)

# ── LLM / Claude API ─────────────────────────────────────────────────────────

LLM_CALLS_TOTAL = Counter(
    "sentinel_llm_calls_total",
    "Total calls made to the Anthropic API",
    ["status"],          # labels: "success" | "failure"
)

LLM_CALL_DURATION = Histogram(
    "sentinel_llm_call_duration_seconds",
    "Latency of a single Anthropic API call",
    buckets=[0.5, 1, 2, 3, 5, 8, 12, 20, 30],
)

CIRCUIT_BREAKER_OPEN = Gauge(
    "sentinel_circuit_breaker_open",
    "1 if the LLM circuit breaker is open (Claude unreachable), 0 if closed",
)
