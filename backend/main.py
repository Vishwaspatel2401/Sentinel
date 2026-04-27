# =============================================================================
# FILE: backend/main.py
# WHAT: Entry point for the entire FastAPI backend.
#       Creates the app instance, configures logging, and registers all routers.
# WHY:  Uvicorn (the web server) looks for `app` in main.py to start serving.
#       Every router you create must be registered here with app.include_router().
# FLOW: uvicorn main:app → setup_logging() → FastAPI starts → routers registered → ready
# CONNECTED TO:
#   → core/logging_config.py       — setup_logging() called at startup
#   → api/routers/health.py        — GET /health       — public, no auth
#   → api/routers/alerts.py        — POST /api/v1/alerts    — requires X-API-Key
#   → api/routers/incidents.py     — GET /api/v1/incidents/{id} — requires X-API-Key
#   → api/dependencies/auth.py     — verify_api_key applied to protected routers
# =============================================================================

from fastapi import FastAPI, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import Response
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from pathlib import Path
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from core.logging_config import setup_logging        # structured JSON logging
from config import settings                          # log_level from .env
from api.routers import alerts                       # POST /api/v1/alerts
from api.routers import incidents                    # GET /api/v1/incidents/{id}
from api.routers import health                       # GET /health (public)
from api.dependencies.auth import verify_api_key     # X-API-Key header validation
from api.dependencies.rate_limit import limiter      # shared rate limiter instance

# ── Configure logging FIRST — before the app is created ───────────────────────
# setup_logging() configures the root logger with JSON formatting.
# Every module's logger (logging.getLogger(__name__)) inherits this automatically.
# Must be called before any other module logs anything at startup.
setup_logging(settings.log_level)

# ── Create the FastAPI app ─────────────────────────────────────────────────────
# title and version appear in the auto-generated /docs page.
app = FastAPI(title="Sentinel", version="0.1.0")

# ── Attach the rate limiter ────────────────────────────────────────────────────
# app.state.limiter is where slowapi looks for the limiter at request time.
# _rate_limit_exceeded_handler converts RateLimitExceeded → HTTP 429 JSON response.
# Without this handler, a rate-limited request would return a 500 error instead.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ── Serve the frontend dashboard ──────────────────────────────────────────────
# FRONTEND_DIR is read from config so it works in both environments:
#   Local:  Sentinel/frontend/   (default in config.py)
#   Docker: /app/frontend        (set via FRONTEND_DIR env var in docker-compose)
_FRONTEND_DIR = Path(settings.frontend_dir)
if _FRONTEND_DIR.exists():
    app.mount("/dashboard", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")

# ── Register the health router (NO auth) ──────────────────────────────────────
# /health must be public — load balancers and uptime monitors call it without keys.
# It reveals no sensitive data, so no auth is needed or appropriate.
app.include_router(health.router)

# ── Register protected routers (auth required) ────────────────────────────────
# dependencies=[Depends(verify_api_key)] applies auth to EVERY route in the router.
# Any request missing a valid X-API-Key header gets a 401 before the route runs.
app.include_router(alerts.router,    dependencies=[Depends(verify_api_key)])
app.include_router(incidents.router, dependencies=[Depends(verify_api_key)])

# ── Prometheus metrics (/metrics) ─────────────────────────────────────────────
# Public endpoint — Prometheus scrapes this without auth.
# Returns all registered metrics in Prometheus text format.
# Only expose on internal network in production (nginx should block external access).
@app.get("/metrics", include_in_schema=False)
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
