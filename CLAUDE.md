# CLAUDE.md

Personal running analytics platform (FastAPI + SQLite + React/Vite). Dark-only, single-user, private via Tailscale (`*.ts.net`) on Oracle ARM64. Single source of truth: `docs/PLAN.md`.

## Working Mode & Token Efficiency (Opus / Sonnet)
- **Be Terse & Direct:** Output code, diffs, and direct answers with minimal conversational preamble. Do not summarize changes unless asked.
- **Calibrate to `docs/PLAN.md`:** Work strictly milestone-by-milestone (M0 -> M6 for MVP). NEVER implement future scope ahead of time (no file uploads before M7, no multi-user, no predictive models).
- **No Speculative Reasoning:** Decisions tagged `[DECISIONE]` and `[FATTO]` in `docs/PLAN.md` are settled. Do not re-debate or propose alternatives unless a technical blocker occurs.
- **Surgical Edits:** Make minimal, targeted changes. Do not refactor or rewrite functioning modules.
- **Run Targeted Tests:** Run only relevant test files (e.g. `pytest tests/unit/test_metrics.py`) during iteration to save context and tokens.

## Essential Commands
- **Backend Dev:** `uvicorn app.main:app --reload --host 127.0.0.1 --port 8000`
- **Worker:** `python -m app.worker`
- **Test:** `pytest -q` (or `pytest tests/unit/test_<target>.py`)
- **Lint & Format:** `ruff check . --fix && ruff format .`
- **Type Check:** `mypy app` | `npm run typecheck` (in `web/`)
- **DB Migrations:** `alembic revision --autogenerate -m "<msg>"` | `alembic upgrade head`
- **Frontend Dev:** `npm run dev` (in `web/`) | **Build:** `npm run build`
- **API Types:** `npx openapi-typescript http://127.0.0.1:8000/openapi.json -o web/src/api/schema.d.ts`
- **Deploy:** `./scripts/deploy.sh` | **Health:** `curl -f http://127.0.0.1:8000/healthz`

## Core Invariants & Architecture Rules
- **Data & Units:** SI units internally (meters, seconds, m/s, bpm, steps/min). **Never store pace** (always derive min/km). Store UTC `start_time_utc` + IANA `timezone`. Derive `local_date` (YYYY-MM-DD).
- **Epistemic Classes:** Tag metrics: measured (sensor), calculated (pure math), estimated (`est.`), model (`model`).
- **Data Model:** Decouple `activities` (canonical) from `source_records` (raw provider data). Internal autoincrement PK only (never Strava ID). **No `users` table or `user_id`** (single-user, ADR-15).
- **Storage & State:** SQLite in WAL mode (`foreign_keys=ON`, `busy_timeout=5000`). Streams as gzip-compressed JSON blobs in `streams`. Each activity import/recompute is a single atomic transaction.
- **Job Queue:** SQLite `jobs` table with atomic claims and heartbeat. No Celery/Redis.
- **Strava Integration:** Self-limit to 90 reads / 15 min, 900 / day. Rotate refresh tokens atomically on every refresh. Base URL must be configurable (migrates to `api-v3.strava.com` by 2027-01-04). Polling every 30m; no webhooks.
- **Security:** Bind only to `127.0.0.1:8000`. Prod auth = IP allowlist (OCI Security List + UFW on 443) behind Caddy, which injects the `Tailscale-User-Login` header the app checks. Mutating API calls require `Content-Type: application/json` and `X-Corsa: 1`. Zero secrets in git.
- **UI Guidelines:** Pure dark-only. Semantic colors: Pace=Blue, HR=Red, Elevation=Gray, Cadence=Purple, Power=Amber. All numbers must use `tabular-nums`. Pace Y-axis inverted (faster = higher). Responsive down to 375px.
