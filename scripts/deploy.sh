#!/usr/bin/env bash
# Run on the VM from the repo root (/opt/corsa). Rollback: git checkout <tag> && ./scripts/deploy.sh
set -euo pipefail
cd "$(dirname "$0")/.."
DATA_DIR=${DATA_DIR:-$PWD/data}

git pull --ff-only
docker compose build

if [ -f "$DATA_DIR/corsa.db" ]; then
  sqlite3 "$DATA_DIR/corsa.db" ".backup $DATA_DIR/pre-deploy-$(date +%s).db"
  ls -1t "$DATA_DIR"/pre-deploy-*.db | tail -n +6 | xargs -r rm -f
fi

docker compose up -d

for _ in $(seq 30); do
  curl -fsS http://127.0.0.1:8000/healthz >/dev/null 2>&1 && { echo "deploy OK"; exit 0; }
  sleep 2
done
echo "healthz FAILED. Logs: docker compose logs --tail=100 api" >&2
echo "Rollback: git checkout <prev-tag> && ./scripts/deploy.sh; if migration not backward-compatible, restore $DATA_DIR/pre-deploy-*.db over corsa.db (docker compose stop first)" >&2
exit 1
