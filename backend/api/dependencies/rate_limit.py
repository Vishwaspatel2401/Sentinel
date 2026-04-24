# =============================================================================
# FILE: backend/api/dependencies/rate_limit.py
# WHAT: Configures the slowapi rate limiter shared across all routers.
# WHY:  Without rate limiting, anyone with a valid API key can:
#         - POST thousands of alerts/minute → each triggers 4 LLM calls
#         - Run up hundreds of dollars in Anthropic API costs in minutes
#         - Exhaust DB connection pool with rapid GET requests
#       Rate limiting caps the blast radius of a leaked or abused key.
# KEY FUNC — rate limit by API key, not IP:
#   Rate limiting by IP breaks behind load balancers (all traffic looks like
#   one IP). We already have auth, so the API key is the right identifier —
#   each key gets its own independent counter.
#   If the header is missing (anonymous request), fall back to IP — but those
#   requests will already be rejected by verify_api_key before they count.
# CONNECTED TO:
#   → backend/main.py               — attaches limiter to app.state
#   → backend/api/routers/alerts.py — @limiter.limit("20/minute")
#   → backend/api/routers/incidents.py — @limiter.limit("60/minute")
# =============================================================================

from fastapi import Request
from slowapi import Limiter


def get_api_key_or_ip(request: Request) -> str:
    """
    Rate limit key function — returns the X-API-Key header if present,
    falls back to the client IP address.

    Using the API key means each key gets its own independent counter.
    Two different keys can each make 20 requests/minute — they don't share quota.
    This is the right behaviour: rate limiting punishes the abuser, not everyone.
    """
    return request.headers.get("X-API-Key") or request.client.host


# Single limiter instance — imported by routers and main.py.
# Using the same instance is critical: if alerts.py and incidents.py created
# their own Limiter objects, they'd have separate counters and the limits
# wouldn't work correctly.
limiter = Limiter(key_func=get_api_key_or_ip)
