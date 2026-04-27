# =============================================================================
# FILE: backend/core/constants.py
# WHAT: Shared constants used across multiple modules.
# WHY:  QUEUE_KEY and DEAD_KEY were duplicated in 4 files (alerts.py,
#       incidents.py, health.py, investigation_worker.py). A single source
#       of truth means a key name change requires editing exactly one file.
# =============================================================================

# Redis queue keys — must match across all producers and consumers
QUEUE_KEY = "sentinel:alert:queue"   # main alert queue — workers BLPOP from here
DEAD_KEY  = "sentinel:alert:dead"    # dead letter queue — failed jobs land here
