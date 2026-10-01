#!/usr/bin/env bash
# Restore latest restic snapshot on a clean machine. Usage: restore.sh [target_dir=/opt/corsa/data]
# Stop the stack first if restoring over a live one: docker compose stop
set -euo pipefail
set -a; . "${BACKUP_ENV:-/etc/corsa/backup.env}"; set +a
DATA_DIR=${1:-/opt/corsa/data}
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT

restic restore latest --tag corsa --target "$TMP"
# snapshot paths are stored as /<tmpdir>/backup.db -> locate it
db=$(find "$TMP" -name backup.db -print -quit)
[ -n "$db" ] || { echo "backup.db not found in snapshot" >&2; exit 1; }
sqlite3 "$db" "PRAGMA integrity_check;" | grep -qx ok || { echo "integrity_check failed" >&2; exit 1; }

mkdir -p "$DATA_DIR"
rm -f "$DATA_DIR"/corsa.db-wal "$DATA_DIR"/corsa.db-shm
cp "$db" "$DATA_DIR/corsa.db"
[ -d "$TMP$DATA_DIR/uploads" ] && cp -a "$TMP$DATA_DIR/uploads" "$DATA_DIR/"
chown -R 1000:1000 "$DATA_DIR" 2>/dev/null || true   # container user uid 1000
echo "restored to $DATA_DIR; now: docker compose up -d, then check activity count"
sqlite3 "$DATA_DIR/corsa.db" "SELECT count(*) || ' activities' FROM activities;" || true
