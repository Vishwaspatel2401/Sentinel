# =============================================================================
# FILE: scripts/seed_db.py
# WHAT: Inserts mock data into Postgres so the investigation pipeline has
#       real evidence to find and reason over.
# WHY:  In production, logs come from real services and deploys from CI/CD.
#       For testing, we seed realistic data so the full agent pipeline
#       (ClassifierAgent → InvestigatorAgent → HypothesisAgent → ResponderAgent)
#       has something meaningful to investigate.
# FLAGS:
#   --reset              Wipes all existing data before inserting fresh rows.
#                        Required for re-running the demo — without it, old log
#                        timestamps drift outside the 30-minute window and
#                        agents find no evidence.
#   --scenario <name>    Which incident to simulate. Defaults to "db_pool".
#                        Available scenarios:
#                          db_pool          — DB connection pool exhaustion (default)
#                          memory_leak      — OOMKilled pod, heap growing unbounded
#                          high_cpu         — CPU throttling, N² algorithm regression
#                          network_timeout  — DNS failure, downstream service unreachable
#                          deploy_regression — generic bad deploy, error spike post-release
# RUN FROM PROJECT ROOT:
#   python3 scripts/seed_db.py                                    # db_pool scenario
#   python3 scripts/seed_db.py --reset                            # wipe + re-seed db_pool
#   python3 scripts/seed_db.py --reset --scenario memory_leak     # test OOM investigation
#   python3 scripts/seed_db.py --reset --scenario high_cpu        # test CPU investigation
#   python3 scripts/seed_db.py --reset --scenario network_timeout # test network investigation
#   python3 scripts/seed_db.py --reset --scenario deploy_regression
# CONNECTED TO:
#   → backend/db/models.py       — Incident, LogEntry, Deploy, Resolution ORM models
#   → backend/config.py          — database_url from .env
#   → services/log_service.py    — queries log_entries by service_name + timestamp
#   → services/deploy_service.py — queries deploys by service_name + deployed_at
# =============================================================================

import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from datetime import datetime, timedelta, timezone
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from db.models import Incident, LogEntry, Deploy, Resolution
from config import settings


engine = create_async_engine(settings.database_url, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


# =============================================================================
# SCENARIOS
# Each scenario is a dict with everything needed to seed one realistic incident.
# Keys:
#   incident   — fields for the Incident row (what the alert looks like)
#   logs       — list of (message, count) tuples — each message repeated `count` times
#   deploy     — fields for the Deploy row (what changed before the incident)
#                set to None if the scenario has no recent deploy
#   summary    — what gets printed after seeding so you know what to expect
# =============================================================================

SCENARIOS = {

    # ── Scenario 1: DB connection pool exhaustion ──────────────────────────────
    # The canonical Sentinel demo. A deploy reduced pool_size from 20 to 5.
    # Agents should find the deploy, correlate it with connection refused errors,
    # and suggest a rollback + pool_size increase.
    "db_pool": {
        "incident": {
            "service_name": "payments-api",
            "severity": "P1",
            "title": "DB connection timeout — error rate 12.3%",
            "description": (
                "Error rate on /charge endpoint exceeded 5% threshold. "
                "Multiple connection refused errors. Started 02:14 AM."
            ),
            "error_type": "db_timeout",
            "source": "prometheus",
        },
        "logs": [
            ("connection refused", 847),   # dominant signal — pool exhausted
        ],
        "deploy": {
            "version": "v2.4.1",
            "deployed_at_offset_minutes": 47,   # deployed 47 min before "now"
            "deployed_by": "ci-bot",
            "diff_summary": "pool_size: 20→5",
        },
        "summary": "847 'connection refused' errors. Deploy v2.4.1 changed pool_size: 20→5 (47 min ago).",
    },

    # ── Scenario 2: Memory leak / OOMKilled ───────────────────────────────────
    # A pod keeps restarting because it runs out of memory. Heap grew unbounded
    # because a new in-memory cache was added with no eviction policy.
    # Agents should find the OOMKilled pattern + the deploy that added the cache.
    "memory_leak": {
        "incident": {
            "service_name": "order-service",
            "severity": "P1",
            "title": "Pod OOMKilled — repeated crash loop",
            "description": (
                "order-service pod restarting every 8-12 minutes. "
                "Exit code 137 (OOMKilled). Heap memory growing steadily "
                "from 512MB to 1.8GB before each crash. Started after v3.1.0 deploy."
            ),
            "error_type": "oom",
            "source": "kubernetes",
        },
        "logs": [
            ("OOMKilled: container exceeded memory limit 1792Mi", 23),
            ("java.lang.OutOfMemoryError: Java heap space", 156),
            ("GC overhead limit exceeded", 89),
            ("heap space exhausted — forcing full GC", 412),
        ],
        "deploy": {
            "version": "v3.1.0",
            "deployed_at_offset_minutes": 95,
            "deployed_by": "alice",
            "diff_summary": "added in-memory product cache (no TTL, no max size configured)",
        },
        "summary": "OOMKilled + heap exhaustion errors. Deploy v3.1.0 added unbounded cache (95 min ago).",
    },

    # ── Scenario 3: High CPU / algorithm regression ───────────────────────────
    # A new feature introduced an O(N²) loop over the user's order history.
    # Works fine in testing with small datasets, blows up in production with
    # users who have thousands of orders.
    # Agents should find CPU throttling + the deploy that introduced the regression.
    "high_cpu": {
        "incident": {
            "service_name": "recommendation-service",
            "severity": "P2",
            "title": "CPU throttling — p99 latency 12s",
            "description": (
                "recommendation-service CPU at 98% sustained. "
                "p99 response time increased from 200ms to 12 seconds. "
                "Requests timing out. Began after v1.8.2 deploy at 14:30 UTC."
            ),
            "error_type": "high_cpu",
            "source": "datadog",
        },
        "logs": [
            ("CPU throttling detected — container over limit", 203),
            ("request timeout after 10000ms", 541),
            ("thread pool exhausted — rejecting request", 88),
            ("slow operation detected: compute_recommendations took 9847ms", 312),
        ],
        "deploy": {
            "version": "v1.8.2",
            "deployed_at_offset_minutes": 35,
            "deployed_by": "bob",
            "diff_summary": "new personalisation algorithm — O(N²) loop over user order history",
        },
        "summary": "CPU throttling + timeout errors. Deploy v1.8.2 added O(N²) algorithm (35 min ago).",
    },

    # ── Scenario 4: Network timeout / DNS failure ─────────────────────────────
    # A Kubernetes service selector was misconfigured in a deploy, causing the
    # inventory service to stop routing to any pods. DNS resolves fine but
    # connections time out because no pods are selected.
    # Agents should identify the network pattern + the deploy that broke routing.
    "network_timeout": {
        "incident": {
            "service_name": "checkout-service",
            "severity": "P1",
            "title": "Downstream timeout — inventory-service unreachable",
            "description": (
                "checkout-service failing to reach inventory-service. "
                "All calls to inventory-service timing out after 5s. "
                "DNS resolves correctly but connections hang. "
                "Error rate 100% on /checkout endpoint."
            ),
            "error_type": "network_timeout",
            "source": "prometheus",
        },
        "logs": [
            ("connection timed out: inventory-service:8080", 634),
            ("context deadline exceeded after 5000ms", 634),
            ("no healthy upstream: inventory-service", 201),
            ("circuit breaker OPEN: inventory-service", 87),
        ],
        "deploy": {
            "version": "v2.0.1",
            "deployed_at_offset_minutes": 22,
            "deployed_by": "ci-bot",
            "diff_summary": "inventory-service: updated pod labels — selector mismatch introduced",
        },
        "summary": "Timeout + circuit breaker errors. Deploy v2.0.1 broke service selector (22 min ago).",
    },

    # ── Scenario 5: Generic deploy regression ────────────────────────────────
    # A deploy introduced a missing environment variable in production.
    # The service starts but crashes on the first request that reads the variable.
    # Agents should find the crash pattern + correlate with the deploy timing.
    "deploy_regression": {
        "incident": {
            "service_name": "auth-service",
            "severity": "P1",
            "title": "auth-service 500 errors — KeyError on JWT_SECRET",
            "description": (
                "auth-service returning 500 on all /login and /refresh endpoints. "
                "KeyError: JWT_SECRET in logs. Environment variable present in "
                "staging but missing in production deployment. Started immediately "
                "after v4.2.0 deploy."
            ),
            "error_type": "application_error",
            "source": "sentry",
        },
        "logs": [
            ("KeyError: 'JWT_SECRET' — environment variable not set", 923),
            ("500 Internal Server Error on POST /login", 923),
            ("authentication failed — service misconfigured", 412),
        ],
        "deploy": {
            "version": "v4.2.0",
            "deployed_at_offset_minutes": 12,
            "deployed_by": "carol",
            "diff_summary": "migrated JWT config to new secret manager — JWT_SECRET not added to prod environment",
        },
        "summary": "923 KeyError crashes. Deploy v4.2.0 missing JWT_SECRET in prod env (12 min ago).",
    },
}


# =============================================================================
# RESET
# =============================================================================

async def reset(db) -> None:
    # Delete in FK-safe order: children before parents.
    # Resolution and LogEntry both reference Incident — delete them first.
    print("Resetting database...")
    await db.execute(delete(Resolution))
    await db.execute(delete(LogEntry))
    await db.execute(delete(Deploy))
    await db.execute(delete(Incident))
    await db.commit()
    print("✓ All existing data cleared.")


# =============================================================================
# SEED
# =============================================================================

async def seed(scenario_name: str, do_reset: bool) -> None:

    # Validate scenario name early — fail with a clear message, not a KeyError
    if scenario_name not in SCENARIOS:
        valid = ", ".join(SCENARIOS.keys())
        print(f"✗ Unknown scenario '{scenario_name}'. Valid options: {valid}")
        sys.exit(1)

    scenario = SCENARIOS[scenario_name]
    now = datetime.now(timezone.utc)

    print(f"Seeding scenario: '{scenario_name}'")

    async with AsyncSessionLocal() as db:

        if do_reset:
            await reset(db)

        # ── Insert Incident ────────────────────────────────────────────────────
        incident = Incident(
            **scenario["incident"],   # unpack all incident fields from the scenario dict
            status="investigating",
        )
        db.add(incident)
        await db.flush()              # get incident.id before commit — needed as FK

        # ── Insert LogEntry rows ───────────────────────────────────────────────
        # Each scenario defines log messages + how many times each appears.
        # Timestamps spread backwards from now so they fall inside the 30-min window.
        logs = []
        total_logs = 0
        for message, count in scenario["logs"]:
            for i in range(count):
                logs.append(LogEntry(
                    incident_id=incident.id,
                    service_name=scenario["incident"]["service_name"],
                    level="ERROR",
                    message=message,
                    # Spread timestamps so they look like a real spike, not all at once.
                    # Use total_logs as the offset so each message gets unique timestamps.
                    timestamp=now - timedelta(seconds=(total_logs + i) * 2),
                ))
            total_logs += count

        db.add_all(logs)

        # ── Insert Deploy row (if scenario has one) ────────────────────────────
        deploy_data = scenario.get("deploy")
        if deploy_data:
            deploy = Deploy(
                service_name=scenario["incident"]["service_name"],
                version=deploy_data["version"],
                deployed_at=now - timedelta(minutes=deploy_data["deployed_at_offset_minutes"]),
                deployed_by=deploy_data["deployed_by"],
                diff_summary=deploy_data["diff_summary"],
            )
            db.add(deploy)

        await db.commit()

    # ── Print summary ──────────────────────────────────────────────────────────
    print(f"✓ Inserted 1 incident   (id: {incident.id})")
    print(f"✓ Inserted {total_logs} log entries  (service: {scenario['incident']['service_name']})")
    if deploy_data:
        print(f"✓ Inserted 1 deploy  ({deploy_data['version']}, {deploy_data['diff_summary']}, "
              f"{deploy_data['deployed_at_offset_minutes']} min ago)")
    else:
        print("✓ No deploy inserted for this scenario")
    print(f"\nScenario summary: {scenario['summary']}")
    print("\nReady to test:")
    print("  1. Start the worker: python -m workers.investigation_worker")
    print("  2. POST an alert:    curl -X POST http://localhost:8000/api/v1/alerts ...")


# =============================================================================
# ENTRYPOINT
# =============================================================================

if __name__ == "__main__":
    # Parse --reset and --scenario flags from command-line arguments.
    # We do this manually (not argparse) to keep the script dependency-free.
    #
    # Examples:
    #   python3 scripts/seed_db.py
    #   python3 scripts/seed_db.py --reset
    #   python3 scripts/seed_db.py --reset --scenario memory_leak
    #   python3 scripts/seed_db.py --scenario high_cpu --reset   (order doesn't matter)

    args = sys.argv[1:]   # everything after the script name

    do_reset = "--reset" in args

    # Find --scenario value — look for "--scenario" then take the next item
    scenario_name = "db_pool"   # default
    if "--scenario" in args:
        idx = args.index("--scenario")
        if idx + 1 < len(args):
            scenario_name = args[idx + 1]
        else:
            print("✗ --scenario requires a value. Example: --scenario memory_leak")
            sys.exit(1)

    asyncio.run(seed(scenario_name=scenario_name, do_reset=do_reset))
