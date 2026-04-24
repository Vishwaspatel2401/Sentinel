# =============================================================================
# FILE: backend/api/dependencies/auth.py
# WHAT: FastAPI dependency that validates the X-API-Key header on every
#       protected request. Inject it into any route to require authentication.
# WHY:  Without auth, anyone who discovers your URL can:
#         - POST fake alerts that trigger real investigations (costs money)
#         - GET all your incident data (data leak)
#       An API key is the simplest effective protection for a backend API.
#       More advanced options (OAuth2, JWT) are overkill until you have users.
# HOW TO USE:
#   In a router file, add the dependency to the route decorator:
#     @router.post("", dependencies=[Depends(verify_api_key)])
#   Or per-router — applies to every route in that router:
#     router = APIRouter(dependencies=[Depends(verify_api_key)])
# HOW TO CALL THE API:
#   curl -H "X-API-Key: your-key-here" http://localhost:8000/api/v1/alerts
# CONNECTED TO:
#   ← config.py          — api_secret_key read from .env
#   → api/routers/alerts.py   — applied to POST /api/v1/alerts
#   → api/routers/incidents.py — applied to GET /api/v1/incidents/{id}
#   ✗ api/routers/health.py   — NOT applied — health check must be public
# =============================================================================

import logging
from fastapi import Security, HTTPException, status
from fastapi.security import APIKeyHeader
from config import settings

logger = logging.getLogger(__name__)

# APIKeyHeader tells FastAPI to look for a header named "X-API-Key".
# auto_error=False means FastAPI won't automatically reject the request
# if the header is missing — we handle that ourselves below with a clear message.
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(api_key: str = Security(api_key_header)) -> str:
    """
    FastAPI dependency — validates the X-API-Key header.

    Returns the key if valid. Raises HTTP 401 if missing or wrong.

    Inject into routes with: Depends(verify_api_key)
    """
    # Check both: key is present AND matches the configured secret.
    # We check `not api_key` first to give a better error message when
    # the header is missing entirely vs. when it's present but wrong.
    if not api_key:
        logger.warning("Request rejected — X-API-Key header missing")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key. Add header: X-API-Key: <your-key>",
        )

    if api_key != settings.api_secret_key:
        # Log at WARNING — repeated failures here may indicate a brute-force attempt.
        # Do NOT log the actual key provided — that would put secrets in your logs.
        logger.warning("Request rejected — invalid X-API-Key provided")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key.",
        )

    return api_key
