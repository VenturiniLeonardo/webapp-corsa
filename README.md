# Corsa

[![CI](https://github.com/VenturiniLeonardo/webapp-corsa/actions/workflows/ci.yml/badge.svg)](https://github.com/VenturiniLeonardo/webapp-corsa/actions/workflows/ci.yml)

Piattaforma personale di analisi della corsa: importa le attività da file (export Strava .zip, FIT/GPX/TCX, Health Auto Export), le salva in un modello dati indipendente dal provider e calcola metriche proprie (split, best effort, zone FC, efficienza). Single-user, dark-only, accessibile solo via Tailscale.

Riferimento completo: [`docs/PLAN.md`](docs/PLAN.md) · Operazioni: [`docs/RUNBOOK.md`](docs/RUNBOOK.md)

## Stack

FastAPI · SQLite (WAL) · worker con job queue su SQLite · React + TypeScript + Vite · ECharts · MapLibre · Docker Compose (ARM64)

## Sviluppo

Requisiti: Python ≥ 3.13, Node 20.

```bash
cp .env.example .env            # compila i valori
pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
python -m app.worker            # in un secondo terminale
cd web && npm ci && npm run dev
```

## Test e qualità

| Cosa | Comando |
|------|---------|
| Lint + format | `ruff check . && ruff format --check .` |
| Type check | `mypy app` · `npm run typecheck` (in `web/`) |
| Unit + integration | `pytest -q` |
| Frontend | `npm run lint && npm test` (in `web/`) |
| E2E (Playwright) | `npm ci && npx playwright install chromium && (cd web && npm run build) && npx playwright test` |

## CI/CD

Ogni push su `main` e ogni PR eseguono [`ci.yml`](.github/workflows/ci.yml):

- **backend**: ruff, mypy strict, pytest con coverage
- **frontend**: oxlint, tsc, vitest, build di produzione
- **e2e**: Playwright su DB seedato + SPA buildata
- **docker**: build immagine, migrazioni Alembic e smoke test `/healthz`
- **security**: gitleaks (segreti), pip-audit, npm audit

Un tag `vX.Y.Z` lancia [`deploy.yml`](.github/workflows/deploy.yml): stessa suite CI come gate, poi GitHub Release con note automatiche. Dependabot aggiorna settimanalmente pip, npm, Docker e Actions.

## Deploy

La VM è privata (Tailscale), quindi il deploy resta manuale e parte solo da un tag con CI verde:

```bash
git fetch --tags && git checkout vX.Y.Z && ./scripts/deploy.sh
```

Lo script fa backup SQLite pre-deploy, build, `docker compose up -d` e attende `/healthz`. Rollback e disaster recovery: [`docs/RUNBOOK.md`](docs/RUNBOOK.md).

## Sicurezza

Bind solo su `127.0.0.1:8000`, auth tramite header `Tailscale-User-Login`, chiamate mutanti con `X-Corsa: 1` e `Content-Type: application/json`. Nessun segreto nel repo (`.env` ignorato, gitleaks in CI).
