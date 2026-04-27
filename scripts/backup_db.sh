#!/usr/bin/env bash
# =============================================================================
# FILE: scripts/backup_db.sh
# WHAT: Creates a compressed Postgres backup and prunes backups older than 7 days.
# WHY:  Named Docker volumes (postgres_data) aren't backed up by default.
#       If the host dies or the volume is corrupted, all incident data is gone.
#       This script runs pg_dump inside the live postgres container —
#       no downtime, no external tools, works on any server with Docker.
#
# OUTPUT:
#   ./backups/sentinel_YYYY-MM-DD_HH-MM.sql.gz   (gzip-compressed SQL dump)
#
# RETENTION:
#   Keeps the last 7 days of backups. Files older than 7 days are deleted.
#   7 days × ~5-50 MB per backup = 35-350 MB disk usage (very low).
#
# HOW TO USE:
#   Manual:    ./scripts/backup_db.sh
#   Cron:      0 3 * * * cd /path/to/Sentinel && ./scripts/backup_db.sh >> logs/backup.log 2>&1
#              (runs at 3 AM every day)
#
# RESTORE:
#   gunzip -c backups/sentinel_2025-01-15_03-00.sql.gz | \
#     docker exec -i sentinel-postgres-1 psql -U sentinel -d sentinel
#
# REQUIREMENTS:
#   - Run from the project root
#   - .env.production must exist
#   - The postgres container must be running
# =============================================================================

set -euo pipefail

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

step() { echo -e "\n${BLUE}▶ $1${NC}"; }
ok()   { echo -e "${GREEN}✓ $1${NC}"; }
warn() { echo -e "${YELLOW}⚠ $1${NC}"; }
fail() { echo -e "${RED}✗ $1${NC}"; exit 1; }

# ── Config ────────────────────────────────────────────────────────────────────
BACKUP_DIR="./backups"
RETENTION_DAYS=7
TIMESTAMP=$(date +"%Y-%m-%d_%H-%M")
BACKUP_FILE="${BACKUP_DIR}/sentinel_${TIMESTAMP}.sql.gz"

# ── Sanity checks ─────────────────────────────────────────────────────────────
step "Pre-flight checks"

[[ -f ".env.production" ]] || fail ".env.production not found. Run from the project root."
command -v docker &>/dev/null || fail "docker not installed."

# Load DB credentials from .env.production
POSTGRES_USER=$(grep '^POSTGRES_USER=' .env.production | cut -d= -f2 | tr -d '"' | tr -d "'")
POSTGRES_DB=$(grep '^POSTGRES_DB='   .env.production | cut -d= -f2 | tr -d '"' | tr -d "'")

[[ -n "$POSTGRES_USER" ]] || fail "POSTGRES_USER not found in .env.production"
[[ -n "$POSTGRES_DB"   ]] || fail "POSTGRES_DB not found in .env.production"

# Find the running postgres container
POSTGRES_CONTAINER=$(docker ps --filter "name=postgres" --filter "status=running" --format "{{.Names}}" | head -1)
[[ -n "$POSTGRES_CONTAINER" ]] || fail "No running postgres container found. Is the stack up?"

ok "Postgres container: $POSTGRES_CONTAINER"
ok "Database: $POSTGRES_DB (user: $POSTGRES_USER)"

# ── Create backup directory ────────────────────────────────────────────────────
mkdir -p "$BACKUP_DIR"

# ── Run pg_dump ────────────────────────────────────────────────────────────────
# pg_dump runs INSIDE the postgres container (no client tools needed on the host).
# --no-password: relies on pg_dump connecting as the container's postgres user.
# --clean: includes DROP TABLE statements so restore is idempotent.
# --if-exists: prevents errors if restoring to an empty DB.
# Output is piped through gzip on the host — efficient, no temp files in the container.
step "Running pg_dump → $BACKUP_FILE"

docker exec "$POSTGRES_CONTAINER" \
  pg_dump \
    --username="$POSTGRES_USER" \
    --dbname="$POSTGRES_DB" \
    --clean \
    --if-exists \
    --no-password \
  | gzip > "$BACKUP_FILE"

BACKUP_SIZE=$(du -sh "$BACKUP_FILE" | cut -f1)
ok "Backup written: $BACKUP_FILE ($BACKUP_SIZE)"

# ── Prune old backups ──────────────────────────────────────────────────────────
step "Pruning backups older than ${RETENTION_DAYS} days"

# Count before pruning
BEFORE=$(find "$BACKUP_DIR" -name "sentinel_*.sql.gz" | wc -l | tr -d ' ')

find "$BACKUP_DIR" -name "sentinel_*.sql.gz" -mtime +"$RETENTION_DAYS" -delete

AFTER=$(find "$BACKUP_DIR" -name "sentinel_*.sql.gz" | wc -l | tr -d ' ')
PRUNED=$((BEFORE - AFTER))

if [[ $PRUNED -gt 0 ]]; then
  ok "Pruned $PRUNED old backup(s). $AFTER backup(s) retained."
else
  ok "No old backups to prune. $AFTER backup(s) retained."
fi

# ── List current backups ───────────────────────────────────────────────────────
echo ""
echo "Current backups in $BACKUP_DIR:"
ls -lh "$BACKUP_DIR"/sentinel_*.sql.gz 2>/dev/null | awk '{print "  " $5 "  " $9}' || true

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  Backup complete: sentinel_${TIMESTAMP}.sql.gz (${BACKUP_SIZE})${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "To restore this backup:"
echo "  gunzip -c ${BACKUP_FILE} | \\"
echo "    docker exec -i ${POSTGRES_CONTAINER} psql -U ${POSTGRES_USER} -d ${POSTGRES_DB}"
