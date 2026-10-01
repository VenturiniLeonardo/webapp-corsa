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
RESTIC_REPOSITORY=s3:https://<namespace>.compat.objectstorage.<region>.oraclecloud.com/<bucket>
RESTIC_PASSWORD=...            # password manager
AWS_ACCESS_KEY_ID=...          # OCI Customer Secret Key
AWS_SECRET_ACCESS_KEY=...
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

Docker starts on boot and containers use `restart: unless-stopped`, so no app unit is needed. Healthchecks.io: create checks `backup` (period 24 h, grace 2 h) and `sync` (period 30 min, grace 3 h).

## 2. Deploy / rollback

- Deploy: `cd /opt/corsa && ./scripts/deploy.sh` (keeps last 5 `data/pre-deploy-*.db`). Tag stable releases `vX.Y.Z`.
- Rollback: `git checkout <prev-tag> && ./scripts/deploy.sh`.
- If the migration was not backward-compatible: `docker compose stop && cp data/pre-deploy-<ts>.db data/corsa.db && rm -f data/corsa.db-wal data/corsa.db-shm`, checkout old tag, deploy.

## 3. Disaster recovery

New VM (Oracle reclaimed it, account issue, corruption):

1. Section 1: setup up to (not including) `deploy.sh`; `.env` and `backup.env` from password manager.
2. `git clone` into `/opt/corsa`.
3. `sudo ./scripts/restore.sh` (restores latest snapshot, integrity-checks, prints activity count).
4. `./scripts/deploy.sh` then verify `/healthz` and the activity count in the UI.
5. Re-check Strava connection in Settings (re-authorize if tokens were rotated after the snapshot). Re-enable the backup timer.

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
| Strava client secret | Strava API settings → regenerate → update `.env` → `docker compose up -d` → Settings: verify sync works (refresh token may need re-auth). Refresh tokens rotate automatically on each refresh. |
| OCI Customer Secret Key | OCI console → user → create new key → update `/etc/corsa/backup.env` → `sudo ./scripts/backup.sh` succeeds → delete old key. |
| restic password | `restic key add` (new) → update `backup.env` → `restic key remove <old-id>` → store in password manager. Verify `restic snapshots`. |
| healthchecks URL | Regenerate ping key/UUID → update `backup.env` and `.env` (sync ping) → `docker compose up -d`. |
| Tailscale | Machine key expiry: `sudo tailscale up --ssh --hostname corsa` (re-auth) or disable key expiry in admin console. |

After any `.env` change: update the copy in the password manager, then `docker compose up -d`. Never commit secrets.

## 5. Misc

- Logs: `docker compose logs --tail=100 api worker`; failed jobs in `/sync`.
- Disk: `df -h /opt/corsa/data` (backup fails at ≥ 80%).
- Strava base URL moves to `api-v3.strava.com` by 2027-01-04: change the configurable base URL in `.env`.
