#!/usr/bin/env bash
# Consistent online backup of the SQLite database; keeps the newest $KEEP copies.
#
#   deploy/backup.sh                  # backs up to <data directory>/backups
#   KEEP=30 BACKUP_DIR=/mnt/backup deploy/backup.sh
#
# The copy is made with SQLite's backup API, so it is safe while the application is running (a plain
# file copy could catch the database in the middle of a write). Each copy is checked before it is kept.
# Copy the backups to another machine as well: they live on the same disk as the database.
#
# To restore: stop the service, copy a backup over the database file (as the service user), start it.
set -euo pipefail
# shellcheck source=deploy/common.sh
. "$(dirname "${BASH_SOURCE[0]}")/common.sh"
load_env
cd "$APP_DIR"

PYTHON="${PYTHON:-$APP_DIR/.venv/bin/python}"
KEEP="${KEEP:-14}"
case "$KEEP" in '' | *[!0-9]*) die "KEEP must be a number, got '$KEEP'" ;; esac
[ "$KEEP" -ge 1 ] || die "KEEP must be at least 1"
[ -x "$PYTHON" ] || die "Python not found at $PYTHON (set PYTHON, or run deploy/deploy.sh first)"

# Where is the database file? Ask the application, so the rules for LSP_DATABASE_URL live in one place.
DB_PATH=$("$PYTHON" -c '
from sqlalchemy.engine import make_url
from app.config import database_url_from_env
print(make_url(database_url_from_env()).database or "")')

if [ -z "$DB_PATH" ] || [ ! -f "$DB_PATH" ]; then
  info "No database file yet (${DB_PATH:-in-memory}); nothing to back up"
  exit 0
fi

BACKUP_DIR="${BACKUP_DIR:-$(dirname "$DB_PATH")/backups}"
umask 077
mkdir -p "$BACKUP_DIR"
STAMP=$(date -u +%Y%m%d-%H%M%S)
TARGET="$BACKUP_DIR/seminar-$STAMP-$$.db"
trap 'rm -f -- "$TARGET.partial"' EXIT  # never leave a half-written or rejected copy behind

"$PYTHON" - "$DB_PATH" "$TARGET.partial" <<'PY'
import sqlite3
import sys

source_path, target_path = sys.argv[1:3]
source = sqlite3.connect(source_path)
target = sqlite3.connect(target_path)
try:
    source.backup(target)
    result = target.execute("PRAGMA integrity_check").fetchone()[0]
finally:
    target.close()
    source.close()
if result != "ok":
    raise SystemExit(f"backup failed its integrity check: {result}")
PY
mv "$TARGET.partial" "$TARGET"
info "Backed up $DB_PATH to $TARGET"

# Remove the oldest copies beyond $KEEP (names start with a UTC timestamp, so they sort by age).
find "$BACKUP_DIR" -maxdepth 1 -type f -name 'seminar-*.db' | sort -r | awk -v keep="$KEEP" 'NR > keep' |
  while IFS= read -r old; do
    rm -f -- "$old"
    info "Removed old backup $old"
  done
rm -f -- "$BACKUP_DIR"/seminar-*.db.partial  # leftovers of a run that was killed before it could clean up
