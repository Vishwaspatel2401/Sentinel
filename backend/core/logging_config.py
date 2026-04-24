# =============================================================================
# FILE: backend/core/logging_config.py
# WHAT: Configures structured JSON logging for the entire backend.
#       Call setup_logging() once at startup — every module's logger
#       automatically inherits the format.
# WHY:  print() statements have two problems in production:
#         1. No structure — you can't search for "all logs where incident_id=X"
#         2. No level — you can't filter warnings from debug noise
#       Structured JSON logs fix both. Every log line is a JSON object that
#       Datadog, CloudWatch, or any log aggregator can parse and query.
# HOW IT WORKS:
#   Python's logging module has a hierarchy:
#     root logger → app loggers (e.g. "services.llm_service")
#   setup_logging() configures the ROOT logger once.
#   Every module does: logger = logging.getLogger(__name__)
#   Because __name__ creates a child of the root, the JSON format propagates
#   automatically — no per-module configuration needed.
# LOG FORMAT (JSON):
#   {
#     "timestamp": "2024-01-15T02:14:00Z",
#     "level":     "INFO",
#     "logger":    "services.llm_service",
#     "message":   "Circuit opened",
#     "failure_count": 3
#   }
#   Extra fields (like incident_id) are added via: logger.info("msg", extra={...})
# CONNECTED TO:
#   → backend/main.py              — calls setup_logging() at app startup
#   → backend/workers/...          — calls setup_logging() at worker startup
#   ← every service/agent file     — uses logging.getLogger(__name__)
# =============================================================================

import logging
import sys
from pythonjsonlogger import jsonlogger     # pip install python-json-logger


def setup_logging(log_level: str = "INFO") -> None:
    """
    Configure the root logger with JSON formatting.
    Call this ONCE at application startup before anything else runs.

    Args:
        log_level: "DEBUG" | "INFO" | "WARNING" | "ERROR" — from config/env.
                   INFO is the right default for production.
                   DEBUG floods the logs — use only for local troubleshooting.
    """

    # Get the root logger — configuring this propagates to all child loggers
    # (logging.getLogger("services.llm_service") is a child of the root)
    root_logger = logging.getLogger()

    # Set the minimum level — messages below this are silently dropped.
    # getattr converts the string "INFO" to the integer constant logging.INFO (20).
    level = getattr(logging, log_level.upper(), logging.INFO)
    root_logger.setLevel(level)

    # Remove any existing handlers to avoid duplicate log lines.
    # Uvicorn adds its own handlers at startup — without this, every log
    # line would appear twice.
    root_logger.handlers.clear()

    # Stream handler — writes to stdout.
    # In Docker/Kubernetes, stdout is captured and forwarded to your log aggregator.
    # Never write logs to files in containers — the file grows unbounded and
    # gets lost when the pod restarts.
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)

    # JSON formatter — every log line is one complete JSON object.
    # fmt defines which standard LogRecord fields to include.
    # Extra fields passed via logger.info("msg", extra={"k": "v"}) are
    # automatically appended by JsonFormatter.
    formatter = jsonlogger.JsonFormatter(
        fmt="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",     # ISO 8601 — parseable by every log tool
        rename_fields={
            "asctime":   "timestamp",      # cleaner field name for log aggregators
            "name":      "logger",
            "levelname": "level",
        }
    )
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)

    # Silence noisy third-party loggers that flood the output at DEBUG level.
    # These libraries log internal details we don't care about in production.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("sentence_transformers").setLevel(logging.WARNING)
