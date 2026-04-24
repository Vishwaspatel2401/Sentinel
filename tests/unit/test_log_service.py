# =============================================================================
# FILE: tests/unit/test_log_service.py
# WHAT: Unit tests for LogService.summarize() — the method that condenses
#       raw log lines into a short summary before sending to the LLM.
# WHY:  summarize() is a pure function (no DB, no async, no external calls).
#       Pure functions are the easiest to test — give input, check output.
#       If summarize() breaks, the LLM gets garbage context → wrong root cause.
# WHAT WE TEST:
#   1. Empty log list → "No recent errors found."
#   2. Logs are counted and sorted by frequency
#   3. Only top 5 error patterns returned (not all unique messages)
#   4. Output string includes the total count
# KEY CONCEPT — Unit tests:
#   Each test covers ONE behaviour. If a test fails, you know EXACTLY what broke.
#   We don't test fetch_recent_errors() here — that queries a DB, needs a
#   separate integration test with a real or in-memory database.
# =============================================================================

import pytest
from unittest.mock import MagicMock
from services.log_service import LogService


# ── Helpers ────────────────────────────────────────────────────────────────────

def make_log(message: str) -> MagicMock:
    """Create a fake LogEntry with just the .message attribute we need."""
    log = MagicMock()
    log.message = message
    return log


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestLogServiceSummarize:
    """
    Groups all summarize() tests in one class.
    pytest collects Test* classes automatically — no special registration needed.
    Grouping makes the test output easier to read and failures easier to locate.
    """

    def setup_method(self):
        # setup_method runs before EACH test in this class.
        # LogService needs a DB session, but summarize() never uses it —
        # so we pass a MagicMock. The mock accepts any attribute access without
        # raising errors, which is exactly what we need here.
        self.svc = LogService(db=MagicMock())

    def test_empty_logs_returns_no_errors_message(self):
        # If no logs are found, the summary should say so clearly.
        # This is what the LLM sees when there are no error logs — it should
        # not fabricate errors when we tell it none exist.
        result = self.svc.summarize([])
        assert result == "No recent errors found."

    def test_single_log_counted_correctly(self):
        # One log entry should produce a count of 1.
        logs = [make_log("connection refused")]
        result = self.svc.summarize(logs)

        assert "1 total errors" in result
        assert "connection refused" in result

    def test_duplicate_messages_are_counted_together(self):
        # 5 identical messages should appear as "5x: connection refused",
        # not as 5 separate lines. This is the whole point of summarize().
        logs = [make_log("connection refused")] * 5
        result = self.svc.summarize(logs)

        assert "5 total errors" in result
        assert "5x: connection refused" in result

    def test_most_frequent_error_appears_first(self):
        # The most common error should be listed before less common ones.
        # The LLM should see the dominant error pattern first.
        logs = (
            [make_log("connection refused")] * 10 +
            [make_log("query timeout")] * 3
        )
        result = self.svc.summarize(logs)
        lines = result.splitlines()

        # Find which line has each message
        refused_line = next(l for l in lines if "connection refused" in l)
        timeout_line = next(l for l in lines if "query timeout" in l)

        # connection refused (10x) should appear before query timeout (3x)
        assert result.index(refused_line) < result.index(timeout_line)

    def test_only_top_5_patterns_returned(self):
        # Even if there are 10 unique error messages, only the top 5 should
        # appear in the summary. This controls the token count sent to the LLM.
        logs = [make_log(f"error type {i}") for i in range(10)]
        result = self.svc.summarize(logs)

        # Count how many "error type N" lines appear — should be at most 5
        matching_lines = [l for l in result.splitlines() if "error type" in l]
        assert len(matching_lines) <= 5

    def test_total_count_in_header(self):
        # The first line should report the TOTAL number of logs, not just top-5.
        # "13 total errors. Top patterns:" — engineer needs the real number.
        logs = (
            [make_log("error A")] * 5 +
            [make_log("error B")] * 5 +
            [make_log("error C")] * 3
        )
        result = self.svc.summarize(logs)
        assert "13 total errors" in result
