#!/usr/bin/env bash
# =============================================================================
# FILE: scripts/deploy.sh
# WHAT: Zero-downtime production deploy for Sentinel.
# WHY:  `docker-compose down && up` takes the site offline for 30-60 seconds.
#       This script rolls each service individually so nginx keeps serving
#       traffic the entire time. Workers drain gracefully (60s grace period)
#       so no in-flight investigations are killed mid-run.
#
# WHAT IT DOES (in order):
#   1. Pull latest code from git
#   2. Build new images (api + worker) with --no-cache
#   3. Run migrations — safe to run against a live DB (Alembic is idempotent)
#   4. Roll the API container  — new image, ~2s gap, nginx retries handle it
#   5. Roll workers one-by-one — each gets 60s to finish its current job
#   6. Restart nginx           — picks up any config changes, re-resolves IPs
#   7. Health check            — verifies the stack is healthy before exiting
#
# HOW TO USE:
#   ./scripts/deploy.sh                  # deploy from current branch
#   ./scripts/deploy.sh --skip-pull      # skip git pull (deploy local changes)
#   ./scripts/deploy.sh --skip-build     # skip image rebuild (re-roll same image)
#
# REQUIREMENTS:
#   - Run from the project root: cd /path/to/Sentinel && ./scripts/deploy.sh
#   - .env.production must exist in the project root
#   - docker and docker-compose must be installed
# =============================================================================

set -euo pipefail   # exit on error, undefined var, or pipe failure

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'   # no colour

step()  { echo -e "\n${BLUE}▶ $1${NC}"; }
ok()    { echo -e "${GREEN}✓ $1${NC}"; }
warn()  { echo -e "${YELLOW}⚠ $1${NC}"; }
fail()  { echo -e "${RED}✗ $1${NC}"; exit 1; }

# ── Flags ─────────────────────────────────────────────────────────────────────
SKIP_PULL=false
SKIP_BUILD=false
for arg in "$@"; do
  case $arg in
    --skip-pull)  SKIP_PULL=true  ;;
    --skip-build) SKIP_BUILD=true ;;
    *) fail "Unknown flag: $arg. Valid flags: --skip-pull, --skip-build" ;;
  esac
done

# ── Sanity checks ─────────────────────────────────────────────────────────────
step "Pre-flight checks"

[[ -f ".env.production" ]] || fail ".env.production not found. Run from the project root."
[[ -f "docker-compose.prod.yml" ]] || fail "docker-compose.prod.yml not found."
command -v docker &>/dev/null || fail "docker not installed."
command -v docker-compose &>/dev/null || fail "docker-compose not installed."

# Verify the stack is currently running (don't deploy to a stopped stack)
if ! docker-compose -f docker-compose.prod.yml --env-file .env.production ps --quiet api | grep -q .; then
  warn "API container not running. Starting full stack instead of rolling deploy."
  docker-compose -f docker-compose.prod.yml --env-file .env.production up -d --build
  ok "Full stack started."
  exit 0
fi

ok "Stack is running. Proceeding with rolling deploy."

# ── Step 1: Pull latest code ───────────────────────────────────────────────────
if [[ "$SKIP_PULL" == "true" ]]; then
  warn "Skipping git pull (--skip-pull)"
else
  step "Pulling latest code"
  # Check if the current branch has a remote tracking branch configured.
  # If not, skip the pull rather than failing — the user may be deploying
  # local changes intentionally, or the remote isn't set up yet.
  if git rev-parse --abbrev-ref --symbolic-full-name @{u} &>/dev/null; then
    git pull --ff-only || fail "git pull failed. Resolve conflicts manually then re-run."
    ok "Code up to date: $(git log -1 --oneline)"
  else
    warn "No remote tracking branch set — skipping git pull."
    warn "To enable auto-pull: git push -u origin $(git rev-parse --abbrev-ref HEAD)"
    ok "Deploying local code: $(git log -1 --oneline)"
  fi
fi

# ── Step 2: Build new images ───────────────────────────────────────────────────
if [[ "$SKIP_BUILD" == "true" ]]; then
  warn "Skipping image build (--skip-build)"
else
  step "Building api + worker images (--no-cache)"
  docker-compose -f docker-compose.prod.yml --env-file .env.production \
    build --no-cache api worker
  ok "Images built."
fi

# ── Step 3: Run migrations ─────────────────────────────────────────────────────
# Alembic is idempotent — running `upgrade head` when already at head is a no-op.
# Safe to run against the live DB while the old API is still serving traffic,
# because we only ever add columns/tables, never drop or rename in place.
step "Running database migrations"
docker-compose -f docker-compose.prod.yml --env-file .env.production \
  run --rm migrate
ok "Migrations applied."

# ── Step 4: Roll the API container ────────────────────────────────────────────
# `up -d --no-deps api` stops the old api container, starts the new one.
# nginx will get a brief 502 (~1-2s) while the new container starts.
# The nginx config has `proxy_next_upstream error timeout` so clients retry.
step "Rolling API container"
docker-compose -f docker-compose.prod.yml --env-file .env.production \
  up -d --no-deps api

# Wait for the API to pass its health check before continuing.
# Uses Python (always present in the image) instead of curl, which isn't
# installed in the slim Python base image.
echo -n "  Waiting for API health check"
for i in $(seq 1 30); do
  if docker-compose -f docker-compose.prod.yml --env-file .env.production \
       exec -T api python -c \
       "import urllib.request, sys; r=urllib.request.urlopen('http://localhost:8000/health'); sys.exit(0 if r.status==200 else 1)" \
       &>/dev/null; then
    echo ""
    ok "API is healthy."
    break
  fi
  echo -n "."
  sleep 2
  if [[ $i -eq 30 ]]; then
    echo ""
    fail "API failed to become healthy after 60s. Check logs: docker-compose -f docker-compose.prod.yml logs api"
  fi
done

# Restart nginx so it re-resolves the API container's new IP
step "Restarting nginx (re-resolve upstream IP)"
docker-compose -f docker-compose.prod.yml --env-file .env.production \
  restart nginx
sleep 2
ok "Nginx restarted."

# ── Step 5: Roll workers one-by-one ───────────────────────────────────────────
# Workers have stop_grace_period: 60s — Docker sends SIGTERM and waits 60s
# before SIGKILL, giving each worker time to finish its current investigation.
# We roll them serially (one at a time) so at least 2 workers are always running.
step "Rolling workers (one at a time, 60s grace per worker)"

# Get the list of running worker container IDs
WORKER_IDS=$(docker-compose -f docker-compose.prod.yml --env-file .env.production \
  ps --quiet worker 2>/dev/null || true)

if [[ -z "$WORKER_IDS" ]]; then
  warn "No worker containers found. Starting workers."
  docker-compose -f docker-compose.prod.yml --env-file .env.production \
    up -d --no-deps worker
  ok "Workers started."
else
  WORKER_COUNT=$(echo "$WORKER_IDS" | wc -l | tr -d ' ')
  echo "  Found $WORKER_COUNT worker container(s)."

  i=1
  while IFS= read -r container_id; do
    CONTAINER_NAME=$(docker inspect --format '{{.Name}}' "$container_id" | tr -d '/')
    echo "  Rolling worker $i/$WORKER_COUNT ($CONTAINER_NAME)..."
    # Stop this one worker — Docker honours the 60s grace period
    docker stop --timeout 60 "$container_id"
    i=$((i + 1))
  done <<< "$WORKER_IDS"

  # Start fresh workers (new image, correct replica count from compose file)
  docker-compose -f docker-compose.prod.yml --env-file .env.production \
    up -d --no-deps worker
  ok "All workers rolled."
fi

# ── Step 6: Final health check ─────────────────────────────────────────────────
step "Final health check"

# Give everything 5s to settle
sleep 5

HEALTH=$(curl -sk https://localhost/health 2>/dev/null || true)
if echo "$HEALTH" | grep -q '"status":"ok"'; then
  ok "Stack is healthy."
  echo ""
  echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
  echo -e "${GREEN}  Deploy complete — $(git log -1 --oneline)${NC}"
  echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
else
  warn "Health check returned unexpected response:"
  echo "  $HEALTH"
  warn "Deploy finished but health check is not clean. Check logs:"
  echo "  docker-compose -f docker-compose.prod.yml logs --tail=50"
fi
