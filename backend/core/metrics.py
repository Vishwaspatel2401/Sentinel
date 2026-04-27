# =============================================================================
# FILE: backend/core/metrics.py
# WHAT: Central registry of every Prometheus metric Sentinel exposes.
#       All counters, histograms and gauges are defined here — once.
# WHY:  If each module created its own metrics, you'd get duplicate-registration
#       errors when modules are imported more than once (common in FastAPI).
#       Defining them as module-level singletons here means they're created
#       exactly once, regardless of how many times the module is imported.
# HOW MULTIPROCESS WORKS:
#       The API and workers run in separate Docker containers (separate processes).
#       Standard prometheus_client keeps metrics in-process — the worker's counters
#       would never reach the API's /metrics endpoint.
#
#       When PROMETHEUS_MULTIPROC_DIR is set, prometheus_client writes every
#       metric update to .db files in that directory instead of memory.
#       The API reads ALL .db files (from all workers) when /metrics is called,
#       merging them into one response that Prometheus scrapes.
#
#       Both api and worker containers mount the same Docker volume at
#       /tmp/prometheus_multiproc, so they share the same .db files on disk.
#
# MULTIPROCESS GAUGE MODES (required in multiprocess mode):
#   livesum  — sum values from all live processes (active investigations, queue depth)
#   liveall  — one series per live process (not used here)
#   max      — highest value across all processes (circuit breaker: 1 beats 0)
#   min      — lowest value across all processes (not used here)
#   all      — one series per process pid (not used here)
#
# METRICS:
#   sentinel_investigations_total        — counter, labels: status (resolved/failed)
#   sentinel_investigation_duration_seconds — histogram of pipeline wall time
#   sentinel_queue_depth                 — gauge, labels: queue (alert/dead)
#   sentinel_llm_calls_total             — counter, labels: status (success/failure)
#   sentinel_llm_call_duration_seconds   — histogram of single LLM call latency
#   sentinel_circuit_breaker_open        — gauge: 1 = open (degraded), 0 = closed (healthy)
#   sentinel_active_investigations       — gauge: jobs currently running
# =============================================================================

import os
from prometheus_client import Counter, Histogram, Gauge

# Detect whether we're running in multiprocess mode.
# When PROMETHEUS_MULTIPROC_DIR is set, prometheus_client automatically uses
# file-based storage. We just need to ensure the directory exists.
_multiproc_dir = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
if _multiproc_dir:
    os.makedirs(_multiproc_dir, exist_ok=True)

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

# livesum: sum active counts across all worker processes
ACTIVE_INVESTIGATIONS = Gauge(
    "sentinel_active_investigations",
    "Number of investigations currently running across all workers",
    multiprocess_mode="livesum",
)

# ── Queue ─────────────────────────────────────────────────────────────────────

# livesum: the worker that polls idle updates this; sum is the actual depth
QUEUE_DEPTH = Gauge(
    "sentinel_queue_depth",
    "Current number of items in a Redis queue",
    ["queue"],           # labels: "alert" | "dead"
    multiprocess_mode="livesum",
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

# max: if ANY worker sees the circuit breaker open, report it as open
CIRCUIT_BREAKER_OPEN = Gauge(
    "sentinel_circuit_breaker_open",
    "1 if the LLM circuit breaker is open (Claude unreachable), 0 if closed",
    multiprocess_mode="max",
)
