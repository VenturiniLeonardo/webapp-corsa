#!/usr/bin/env bash
# Every 5 min via systemd timer (RUNBOOK §2): deploy origin/main once its CI run is green.
# The VM pulls; GitHub never runs code here (no self-hosted runner on a public repo).
set -euo pipefail
cd "$(dirname "$0")/.."
git fetch -q origin main
sha=$(git rev-parse origin/main)
[ "$sha" = "$(git rev-parse HEAD)" ] && exit 0
ci=$(curl -fsS -m 20 "https://api.github.com/repos/VenturiniLeonardo/webapp-corsa/actions/runs?head_sha=$sha&event=push" \
  | python3 -c 'import json,sys; r=[w for w in json.load(sys.stdin)["workflow_runs"] if w["name"]=="CI"]; print(r[0]["conclusion"] if r else "")')
[ "$ci" = "success" ] || { echo "origin/main $sha: CI '${ci:-pending}', not deploying"; exit 0; }
exec bash scripts/deploy.sh
