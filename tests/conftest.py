# =============================================================================
# FILE: tests/conftest.py
# WHAT: Shared pytest fixtures and Python path setup for ALL test files.
# WHY:  conftest.py is special — pytest loads it automatically before any test.
#       Fixtures defined here are available to every test in tests/ and its
#       subdirectories without any import. Subfolders can have their own
#       conftest.py to add more local fixtures on top of these.
# KEY CONCEPT — Fixtures:
#   A fixture is a function decorated with @pytest.fixture that sets up a
#   reusable piece of test data or a mock object. Instead of copy-pasting
#   setup code in every test, you declare it once here and inject it by name.
#   Example: def test_something(mock_incident): ... ← pytest injects it
# =============================================================================

import sys
import os
import uuid
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

# ── Python path setup ──────────────────────────────────────────────────────────
# Tests live in tests/ but the source code is in backend/.
# Without this, `from services.log_service import LogService` would fail with
# ModuleNotFoundError because Python doesn't know where backend/ is.
# sys.path.insert(0, ...) adds backend/ as the FIRST place Python looks for modules.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))


# ── Shared model-like objects ──────────────────────────────────────────────────
# We use SimpleNamespace / plain objects instead of real ORM models in unit tests.
# Real ORM models need a DB connection. Unit tests should NEVER touch a real DB.
# These fake objects have the same attributes the real models have.

@pytest.fixture
def mock_incident():
    """A fake Incident object with the same fields as the real ORM model."""
    incident = MagicMock()
    incident.id = uuid.uuid4()
    incident.service_name = "payments-api"
    incident.severity = "P1"
    incident.title = "DB connection timeout — error rate 12.3%"
    incident.description = "connection refused errors on /charge endpoint"
    incident.error_type = "db_timeout"
    incident.source = "prometheus"
    incident.status = "investigating"
    return incident


@pytest.fixture
def mock_deploy():
    """A fake Deploy object with the same fields as the real ORM model."""
    deploy = MagicMock()
    deploy.version = "v2.4.1"
    deploy.deployed_at = datetime(2024, 1, 15, 2, 13, tzinfo=timezone.utc)
    deploy.deployed_by = "ci-bot"
    deploy.diff_summary = "pool_size: 20→5"
    return deploy


@pytest.fixture
def mock_log_entries():
    """A list of fake LogEntry objects — simulates 847 'connection refused' errors."""
    entries = []
    for i in range(10):      # 10 is enough to test summarise logic without being slow
        entry = MagicMock()
        entry.message = "connection refused"
        entry.level = "ERROR"
        entry.service_name = "payments-api"
        entry.timestamp = datetime(2024, 1, 15, 2, 30, tzinfo=timezone.utc)
        entries.append(entry)
    return entries


@pytest.fixture
def mock_llm_service():
    """
    A fake LLMService with AsyncMock for call() and call_with_messages().

    AsyncMock is needed (not MagicMock) because the real methods are async —
    tests that await them need an awaitable mock, not a regular mock.

    Tests customise the return value like:
        mock_llm_service.call.return_value = '{"root_cause": "..."}'
    """
    svc = MagicMock()
    svc.call = AsyncMock(return_value='{"root_cause": "test", "confidence": 0.9}')
    svc.call_with_messages = AsyncMock(return_value="DONE: test evidence summary")
    svc.model = "claude-haiku-4-5-20251001"
    return svc


@pytest.fixture
def base_context(mock_incident):
    """
    The starting blackboard context — same shape as what AgentOrchestrator creates.
    Each agent test can add fields to this as needed.
    """
    return {
        "incident": mock_incident,
        "incident_type": None,
        "evidence_summary": "",
        "root_cause": "",
        "confidence": 0.0,
        "reasoning": "",
        "suggested_fix": "",
        "escalate": False,
        "escalation_reason": "",
        "evidence": [],
    }
