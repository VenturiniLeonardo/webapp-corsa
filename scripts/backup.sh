#!/usr/bin/env bash
# Nightly 03:30 UTC via systemd timer. Needs /etc/corsa/backup.env:
#   RESTIC_REPOSITORY=s3:https://s3.<region>.backblazeb2.com/<bucket>   (Backblaze B2, S3-compatible)
#   RESTIC_PASSWORD=...  AWS_ACCESS_KEY_ID=<B2 keyID>  AWS_SECRET_ACCESS_KEY=<B2 applicationKey>  AWS_DEFAULT_REGION=<region>
#   HC_BACKUP_URL=https://hc-ping.com/<uuid>
set -euo pipefail
set -a; . "${BACKUP_ENV:-/etc/corsa/backup.env}"; set +a
DATA_DIR=${DATA_DIR:-/opt/corsa/data}
TMP=$(mktemp -d)

ping() { [ -z "${HC_BACKUP_URL:-}" ] || curl -fsS -m 10 --retry 3 "$HC_BACKUP_URL$1" >/dev/null || true; }
trap 'rc=$?; rm -rf "$TMP"; [ $rc -eq 0 ] || ping /fail' EXIT
ping /start

# B2 free tier = 10 GB: refuse to upload at/over the cap, fail (alert) at 80%.
LIMIT=${BUCKET_LIMIT_BYTES:-10000000000}
bucket_bytes() { restic stats --mode raw-data --json | grep -o '"total_size":[0-9]*' | cut -d: -f2; }
[ "$(bucket_bytes)" -lt "$LIMIT" ] || { echo "bucket >= $LIMIT bytes, backup skipped" >&2; exit 1; }

sqlite3 "$DATA_DIR/corsa.db" ".backup $TMP/backup.db"
# ponytail: stored under fixed name so restic dedups across nights; uploads/ added only if present
cd "$TMP"
extra=(); [ -d "$DATA_DIR/uploads" ] && extra=("$DATA_DIR/uploads")
restic backup --tag corsa --host corsa backup.db "${extra[@]}"
restic forget --tag corsa --host corsa --keep-daily 7 --keep-weekly 4 --keep-monthly 12 --prune
[ "$(date +%u)" = 7 ] && restic check

use=$(df --output=pcent "$DATA_DIR" | tail -1 | tr -dc 0-9)
if [ "$use" -ge 80 ]; then echo "disk usage ${use}% >= 80%" >&2; exit 1; fi

used=$(bucket_bytes)
if [ "$used" -ge $((LIMIT / 10 * 8)) ]; then echo "bucket usage $used / $LIMIT bytes >= 80%" >&2; exit 1; fi

ping ""
