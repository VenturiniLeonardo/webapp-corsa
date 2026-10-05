# RUNBOOK

Host: Ubuntu 24.04 aarch64 (OCI A1). App in `/opt/corsa`, data in `/opt/corsa/data`, user `corsa`. Bound to `127.0.0.1:8000`; exposed only via `tailscale serve`.

## 1. Fresh VM setup

```bash
# as ubuntu (sudo)
sudo timedatectl set-timezone UTC
sudo apt update && sudo apt -y install git sqlite3 restic curl unattended-upgrades
sudo useradd -m -s /bin/bash corsa
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# Docker (official repo)
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker corsa

# Tailscale (host, not container)
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --ssh --hostname corsa
# enable HTTPS certs in the tailnet admin console, then:
sudo tailscale serve --bg http://127.0.0.1:8000   # verify flag with `tailscale serve --help`
# after verifying SSH over Tailscale: remove all ingress rules in the OCI Security List
# (keep the OCI serial console as emergency access)
```

App:

```bash
sudo mkdir -p /opt/corsa /etc/corsa && sudo chown corsa: /opt/corsa
sudo -iu corsa
git clone <repo-url> /opt/corsa && cd /opt/corsa
mkdir -p data && sudo chown 1000:1000 data
cp <from password manager> .env            # never commit; chmod 600
./scripts/deploy.sh
curl -f http://127.0.0.1:8000/healthz
```

Backup env (`/etc/corsa/backup.env`, root:root, chmod 600):

```
RESTIC_REPOSITORY=s3:https://s3.<region>.backblazeb2.com/<bucket>   # region from the bucket endpoint, e.g. eu-central-003
RESTIC_PASSWORD=...            # password manager
AWS_ACCESS_KEY_ID=...          # Backblaze B2 application key ID (read+write, scoped to the bucket)
AWS_SECRET_ACCESS_KEY=...      # B2 applicationKey (shown once)
AWS_DEFAULT_REGION=<region>    # same as in the endpoint
HC_BACKUP_URL=https://hc-ping.com/<uuid>
```

First time only: `sudo bash -c 'set -a; . /etc/corsa/backup.env; restic init'`, then run `sudo ./scripts/backup.sh` once and confirm the snapshot with `restic snapshots`. **Do this before importing real data.**

systemd units:

```ini
# /etc/systemd/system/corsa-backup.service
[Unit]
Description=Corsa backup
[Service]
Type=oneshot
ExecStart=/opt/corsa/scripts/backup.sh

# /etc/systemd/system/corsa-backup.timer
[Unit]
Description=Corsa nightly backup
[Timer]
OnCalendar=*-*-* 03:30:00 UTC
Persistent=true
[Install]
WantedBy=timers.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now corsa-backup.timer
systemctl list-timers corsa-backup.timer
```

Litestream (continuous DB replica to the same B2 bucket, prefix `litestream/`, ~1 min lag; restic stays for uploads and as second layer): install the arm64 `.deb` from the Litestream releases page, put this in `/etc/litestream.yml` (root, 600), and add a drop-in `/etc/systemd/system/litestream.service.d/env.conf` with `[Service]` / `EnvironmentFile=/etc/corsa/backup.env`:

```yaml
dbs:
  - path: /opt/corsa/data/corsa.db
    replica:
      type: s3
      bucket: corsa-db
      path: litestream
      endpoint: https://s3.<region>.backblazeb2.com
      region: <region>
      access-key-id: ${AWS_ACCESS_KEY_ID}
      secret-access-key: ${AWS_SECRET_ACCESS_KEY}
      sync-interval: 60s
snapshot: {interval: 24h, retention: 72h}
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now litestream && journalctl -u litestream -n 10
```

Before manually replacing `corsa.db` (rollback below): `sudo systemctl stop litestream`, then start it again afterwards.

Docker starts on boot and containers use `restart: unless-stopped`, so no app unit is needed. Healthchecks.io: create checks `backup` (period 24 h, grace 2 h)

## 2. Deploy / rollback

- Deploy: `cd /opt/corsa && ./scripts/deploy.sh` (keeps last 5 `data/pre-deploy-*.db`). Tag stable releases `vX.Y.Z`.
- Auto-deploy: push to `main` -> CI -> within 5 min the VM pulls and deploys (`scripts/autodeploy.sh`, polls the public GitHub API for the `CI` run of `origin/main`; no self-hosted runner, the repo is public). A commit is attempted once: if build or `healthz` fails, roll back as below. Log: `journalctl -u corsa-autodeploy -n 50`. Setup:

```bash
sudo tee /etc/systemd/system/corsa-autodeploy.service >/dev/null <<'EOF'
[Unit]
Description=Corsa auto-deploy (origin/main with green CI)
After=network-online.target docker.service
[Service]
Type=oneshot
User=corsa
ExecStart=/bin/bash /opt/corsa/scripts/autodeploy.sh
EOF
sudo tee /etc/systemd/system/corsa-autodeploy.timer >/dev/null <<'EOF'
[Unit]
Description=Corsa auto-deploy poll
[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
[Install]
WantedBy=timers.target
EOF
sudo systemctl daemon-reload && sudo systemctl enable --now corsa-autodeploy.timer
```
- Rollback: `git checkout <prev-tag> && ./scripts/deploy.sh` (a later push to `main` will deploy main again; `git checkout main` after fixing).
- If the migration was not backward-compatible: `docker compose stop && cp data/pre-deploy-<ts>.db data/corsa.db && rm -f data/corsa.db-wal data/corsa.db-shm`, checkout old tag, deploy.

## 3. Disaster recovery

New VM (Oracle reclaimed it, account issue, corruption):

1. Section 1: setup up to (not including) `deploy.sh`; `.env` and `backup.env` from password manager.
2. `git clone` into `/opt/corsa`.
3. `sudo LITESTREAM_CONFIG=/etc/litestream.yml ./scripts/restore.sh` (needs `litestream` installed and `/etc/litestream.yml` in place; restores the Litestream replica, else the latest restic snapshot; integrity-checks, prints activity count). Without `LITESTREAM_CONFIG` it uses restic only.
4. `./scripts/deploy.sh` then verify `/healthz` and the activity count in the UI.
5. Re-enable the backup timer and `litestream` (the replica generation continues from the restored DB).

Target RTO < 2 h.

### Quarterly drill (no production impact)

On any machine with Docker + restic, `backup.env` available:

```bash
BACKUP_ENV=./backup.env ./scripts/restore.sh /tmp/drill-data
sqlite3 /tmp/drill-data/corsa.db "SELECT count(*), max(start_time_utc) FROM activities;"
# compare with prod: sqlite3 /opt/corsa/data/corsa.db (same query)
rm -rf /tmp/drill-data
```

Pass = integrity ok, count within last-night delta, latest date matches. Also run `restic check` and confirm the healthchecks `backup` check is green. Log the date of the drill here: _(none yet)_.

## 4. Secret rotation

| Secret | Steps |
|--------|-------|
| B2 application key | Backblaze → Application Keys → add new key (scoped to the bucket) → update `/etc/corsa/backup.env` → `sudo ./scripts/backup.sh` succeeds → delete old key. |
| restic password | `restic key add` (new) → update `backup.env` → `restic key remove <old-id>` → store in password manager. Verify `restic snapshots`. |
| healthchecks URL | Regenerate ping key/UUID → update `backup.env`. |
| OpenRouter API key | openrouter.ai → Keys → create new → `OPENROUTER_API_KEY` in `.env` → `docker compose up -d` → delete old key. Empty value disables AI. |
| Tailscale | Machine key expiry: `sudo tailscale up --ssh --hostname corsa` (re-auth) or disable key expiry in admin console. |

After any `.env` change: update the copy in the password manager, then `docker compose up -d`. Never commit secrets.

## 5. Misc

- Logs: `docker compose logs --tail=100 api worker`; failed jobs in `/sync` (Import page).
- Disk: `df -h /opt/corsa/data` (backup fails at ≥ 80%).

## 6. AI analysis (OpenRouter)

Design: `docs/PLAN.md` §26.

- Enable: set `OPENROUTER_API_KEY=<key>` in `.env` (never in git, never in the frontend), `docker compose up -d`. Empty = disabled; the UI says so.
- Free models: in OpenRouter account → Privacy, the `:free` endpoints may require allowing prompt logging/training by the provider. Decide before enabling (§26.6).
- Tuning (`.env`, all optional): `AI_PRIMARY_MODEL`, `AI_FALLBACK_MODEL_1`, `AI_FALLBACK_MODEL_2`, `AI_TIMEOUT_S` (60), `AI_MAX_TOKENS` (4000), `AI_TEMPERATURE` (0.2), `AI_MAX_RETRIES` (1), `AI_RETRY_BACKOFF_S` (2), `AI_MAX_WAIT_S` (10), `AI_DAILY_LIMIT` (40), `AI_MINUTE_LIMIT` (10).
- Usage today: `sqlite3 data/corsa.db "SELECT value FROM settings WHERE key='ai_usage'"`. Reset: delete that row.
- Fallbacks/errors: `docker compose logs api | grep "ai:"`.
- Drop all cached analyses: `sqlite3 data/corsa.db "DELETE FROM ai_analyses"`.
