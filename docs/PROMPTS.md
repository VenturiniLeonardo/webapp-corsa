# Implementation Prompts & Task Guide

This guide breaks down the implementation of [docs/PLAN.md](file:///C:/Users/Leonardo%20Venturini/Desktop/webapp-corsa/docs/PLAN.md) into concrete, self-contained tasks.
Every prompt is formulated in English and strictly adheres to the official Anthropic prompt engineering guidelines for **Claude Sonnet 5.5** ([Prompting Claude Sonnet 5.5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-sonnet-5-5)) and **Claude Opus 5.5** ([Prompting Claude Opus 5.5](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5)).

---

## Strategy & Model Selection Rules

| Model | Effort Level | Target Tasks | Behavioral Guidance & Directives |
|---|---|---|---|
| **Claude Sonnet 5.5** | **Medium** (Standard) / **High** (Multi-file) | Repository scaffolding, containerization, standard CRUD endpoints, UI pages, Tailwind styling, migrations, routine tests. | • Calibrate effort: `Medium` for well-specified tasks, `High` for broader multi-file tasks. Avoid `Low` for agentic coding as it skips real verification and triggers premature check-ins.<br>• Steer initiative & scope: instruct the model to carry work through without premature check-ins, while strictly forbidding unrequested additions or refactors.<br>• Mandatory verification: require a real build/test execution before declaring completion. |
| **Claude Opus 5.5** | **Medium** (Default) / **High** (Complex) | Complex math (splits interpolation, two-pointer best efforts, Theil-Sen estimator), concurrency & state machines (Strava rate-limit backoff, atomic token rotation, SQLite WAL claim), multi-source deduplication, synchronized multi-chart Canvas rendering. | • Calibrate effort: Thinking is always on. Default to `Medium` (matches or exceeds Opus 5 at `High`). Reserve `High` for dense multi-algorithm tasks.<br>• Prevent premature stops: instruct the model never to end turns with a summary announcing next steps without executing them; carry all parts through to completion.<br>• Treat earlier decisions as settled context; avoid re-debating settled architecture.<br>• Negative frontend constraints: explicitly forbid generic AI styling (no cream/light backgrounds, pill buttons, or card bloat). |

### Universal Execution Rules
1. **Fresh Session:** Open a clean session for each milestone or major task to prevent context degradation.
2. **Context Grounding:** Ensure [CLAUDE.md](file:///C:/Users/Leonardo%20Venturini/Desktop/webapp-corsa/CLAUDE.md) is present in the workspace root.
3. **Structured XML Prompts:** All prompts use semantic XML tags (`<task>`, `<context>`, `<requirements>`, `<constraints>`, `<verification>`) to clearly delineate instructions and eliminate ambiguity.
4. **Verification Gate:** Run the designated verification command after each task and confirm clean execution before moving forward.

---

## Milestone 0: Foundations & Scaffolding

### Task 0.1 — Repository Scaffolding & Tooling
- **Model:** Claude Sonnet 5.5
- **Effort:** Medium
- **Prompt:**
```xml
<task>
Implement Task 0.1 of docs/PLAN.md: Project Scaffolding and Tooling.
</task>

<context>
We are building a personal running analytics web application running on an Oracle Cloud ARM64 VM (Ubuntu 24.04) accessed privately via Tailscale. Single-user, dark-only UI, FastAPI backend, SQLite in WAL mode, React/Vite/Tailwind frontend. Refer to docs/PLAN.md §6 and CLAUDE.md.
</context>

<requirements>
1. Directory Structure:
   - app/ (api/, core/, domain/, ingest/, metrics/, worker/)
   - web/ (React + Vite + Tailwind CSS)
   - tests/ (unit/, integration/, fixtures/strava/)
   - scripts/
   - docs/

2. Python Environment (Python 3.13):
   - Create pyproject.toml with runtime dependencies: fastapi, uvicorn[standard], sqlalchemy>=2.0, alembic, pydantic>=2.0, pydantic-settings, httpx, defusedxml.
   - Dev dependencies: pytest, pytest-asyncio, respx, ruff, mypy.
   - Configure ruff (target-version = "py313", line-length = 100) and mypy (strict = true) in pyproject.toml.

3. Frontend Environment in web/:
   - Initialize Vite + React + TypeScript.
   - Configure Tailwind CSS with dark-only color palette matching docs/PLAN.md §12.2:
     background (#0a0a0c), panel surface (#121216), border (#22222a), accent blue (#3b82f6).
   - Install dependencies: @tanstack/react-query, lucide-react, echarts, maplibre-gl.

4. Configuration & Environment:
   - Create root .gitignore covering data/, .env, node_modules/, .pytest_cache/, dist/, build/.
   - Create .env.example containing:
     STRAVA_CLIENT_ID=your_client_id
     STRAVA_CLIENT_SECRET=your_client_secret
     STRAVA_API_BASE=https://www.strava.com/api/v3
     ALLOWED_LOGINS=your_tailscale_username
     DATABASE_URL=sqlite:////data/corsa.db
     ENV=dev
     AUTH_DEV_LOGIN=dev_user
</requirements>

<constraints>
- Keep changes surgical and strictly limited to the files and directories specified above.
- Do not create unrequested placeholder files, extra endpoints, or future scope from M7/M8.
- Keep working until all files are generated and the verification commands pass.
</constraints>

<verification>
Execute real checks to verify the setup:
1. Run `ruff check .`
2. Run `npm --prefix web run build`
Only report the task as done after both commands succeed.
</verification>
```
- **Verification:** `ruff check .` && `npm --prefix web run build`

---

### Task 0.2 — Docker Compose & Multi-Stage Image
- **Model:** Claude Sonnet 5.5
- **Effort:** Medium
- **Prompt:**
```xml
<task>
Implement Task 0.2 of docs/PLAN.md: Docker Compose & Base Container.
</task>

<context>
The application runs as two containerized services (`api` and `worker`) sharing a single SQLite database mounted at `/data`. Memory is capped at 512MB per container on Oracle Cloud ARM64. Refer to docs/PLAN.md §5, §16.4, and CLAUDE.md.
</context>

<requirements>
1. Multi-Stage Dockerfile:
   - Stage 1 (node:20-alpine): Build the React SPA in web/ (outputs static assets).
   - Stage 2 (python:3.13-slim):
     - Install runtime requirements and Python dependencies from pyproject.toml.
     - Copy app/ codebase and alembic migration files.
     - Copy built frontend assets into app/static.
     - Non-root user `corsa` for container execution.

2. docker-compose.yml:
   - Service `api`:
     - Command: `sh -c "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"`
     - Port mapping: strictly bind to `127.0.0.1:8000:8000` (never expose to 0.0.0.0 directly; Tailscale handles incoming traffic).
   - Service `worker`:
     - Command: `python -m app.worker`
   - Shared volume: bind mount `./data:/data` for both services.
   - Resource limits: `deploy.resources.limits.memory: 512M` per service.
   - Logging: `json-file` driver with options `max-size: "10m"` and `max-file: "3"`.
</requirements>

<constraints>
- Follow CLAUDE.md: no unnecessary preamble, concise configuration.
- Bind only to 127.0.0.1:8000. Zero secrets hardcoded in compose files.
- Finish all steps before reporting completion.
</constraints>

<verification>
Validate the configuration using:
`docker compose config`
Ensure the compose specification is syntactically valid and properly formatted.
</verification>
```
- **Verification:** `docker compose config`

---

### Task 0.3 — Config, Healthcheck & Security Middlewares
- **Model:** Claude Sonnet 5.5
- **Effort:** Medium
- **Prompt:**
```xml
<task>
Implement Task 0.3 of docs/PLAN.md: Config, Healthcheck, and Security Middlewares.
</task>

<context>
Security relies on private Tailscale identity headers and CSRF protections for mutating endpoints. The application is single-user. Refer to docs/PLAN.md §11.1, §14.1, §14.2, and CLAUDE.md.
</context>

<requirements>
1. app/core/config.py:
   - Implement `Settings` class using `pydantic-settings` to load `.env`.
   - Validate: STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET, STRAVA_API_BASE, ALLOWED_LOGINS (parsed as comma-separated set), DATABASE_URL, ENV (dev/prod), AUTH_DEV_LOGIN.

2. app/main.py:
   - Initialize FastAPI application.
   - Add endpoint `GET /healthz` returning `{status: "ok", db: true, last_sync_age_s: null, failed_jobs_24h: 0}`.
   - Mount SPA static files from `app/static` with fallback to `index.html`.

3. Security Middlewares:
   - Auth Middleware (docs/PLAN.md §14.2):
     - Read `Tailscale-User-Login` header and verify presence in `ALLOWED_LOGINS`.
     - In `ENV=dev`, if header is missing, fallback to `AUTH_DEV_LOGIN`.
     - Return HTTP 401 Unauthorized if authentication fails. Exempt `/healthz` and `/api/strava/callback`.
   - CSRF & Header Middleware (docs/PLAN.md §14.1):
     - On mutating HTTP methods (`POST`, `PUT`, `PATCH`, `DELETE`), require `Content-Type: application/json` and header `X-Corsa: 1`.
     - Exempt `/api/strava/callback` (OAuth redirect). Return HTTP 403 Forbidden if invalid.
   - Security Headers:
     - Content-Security-Policy allowing `tiles.openfreemap.org` for map tiles.
     - `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, `X-Frame-Options: DENY`.

4. Unit Tests in tests/unit/test_security_middlewares.py:
   - Test 401 when `Tailscale-User-Login` is missing in production mode.
   - Test 403 on POST without `X-Corsa: 1`.
   - Test 200 on `GET /healthz`.
   - Test dev fallback authentication.
</requirements>

<constraints>
- Implement only the specified middlewares and config. Do not add stub routes or future scope.
- Exercise the code with pytest before declaring completion. Syntax-only checks do not count.
</constraints>

<verification>
Run the unit test suite:
`pytest tests/unit/test_security_middlewares.py`
Verify that all middleware assertions pass.
</verification>
```
- **Verification:** `pytest tests/unit/test_security_middlewares.py`

---

### Task 0.4 — Strava OAuth & Test Fixture Fetcher (Spike M0-08)
- **Model:** Claude Sonnet 5.5
- **Effort:** Medium
- **Prompt:**
```xml
<task>
Implement Task 0.4 of docs/PLAN.md: Strava OAuth Handler and Test Fixture Fetcher (M0-08 Spike).
</task>

<context>
We need an initial OAuth handshake to acquire developer credentials and a fixture fetcher script to capture real Strava activities (with anonymized GPS) to validate mapper assumptions. Refer to docs/PLAN.md §9.1, §9.2, §25 (M0-08), and CLAUDE.md.
</context>

<requirements>
1. app/api/strava_auth.py:
   - `GET /api/strava/connect`: Generate random state string, cache with 10-minute expiry, redirect to Strava OAuth URL with `scope=activity:read_all` and `approval_prompt=auto`.
   - `GET /api/strava/callback`: Validate state parameter, exchange authorization code for tokens via Strava token endpoint, verify granted scope contains `activity:read_all`.

2. scripts/fetch_strava_fixtures.py:
   - CLI script taking an access token or reading from environment.
   - Download 5-10 varied activities (GPS + HR, treadmill/indoor, trail run, non-run activity, activity with pauses).
   - Fetch detailed activity JSON and detailed streams JSON (`time`, `distance`, `latlng`, `altitude`, `velocity_smooth`, `heartrate`, `cadence`, `watts`, `moving`).
   - Anonymize GPS coordinates by applying a random fixed offset to lat/lng pairs for privacy.
   - Save artifacts to `tests/fixtures/strava/{activity_id}_detail.json` and `{activity_id}_streams.json`.
</requirements>

<constraints>
- Keep changes focused strictly on auth endpoints and the fixture script.
- Do not build complex database storage yet; tokens in this spike can be logged or returned for testing.
- Finish all steps and verify script argument handling.
</constraints>

<verification>
Verify script syntax and CLI help output:
`python scripts/fetch_strava_fixtures.py --help`
</verification>
```
- **Verification:** `python scripts/fetch_strava_fixtures.py --help`

---

## Milestone 1: Data Model & Pure Metrics Engine

### Task 1.1 — SQLAlchemy Models & Initial Alembic Migration
- **Model:** Claude Sonnet 5.5
- **Effort:** High
- **Prompt:**
```xml
<task>
Implement Task 1.1 of docs/PLAN.md §7: Database Schema and SQLAlchemy Models.
</task>

<context>
SQLite in WAL mode is our single source of truth. We must decouple canonical activities from raw provider records. Single-user system: no users table and no user_id column. All units internally SI. Refer to docs/PLAN.md §7.2, §24 (ADR-15), and CLAUDE.md.
</context>

<requirements>
1. app/core/db.py:
   - Configure SQLAlchemy 2.0 engine with SQLite pragmas on connection:
     `foreign_keys=ON`, `journal_mode=WAL`, `busy_timeout=5000`, `synchronous=NORMAL`.

2. app/domain/models.py Declarative Schema:
   - `activities`: id (INTEGER PK), sport_type (CHECK: run, trail_run, treadmill), name (TEXT), start_time_utc (TEXT ISO-8601), timezone (TEXT IANA), local_date (TEXT YYYY-MM-DD), elapsed_s (INTEGER), moving_s (INTEGER), distance_m (REAL), elev_gain_m (REAL), elev_loss_m (REAL), avg_hr (REAL), max_hr (REAL), avg_cadence_spm (REAL), avg_power_w (REAL), calories_kcal (INTEGER), has_gps (BOOLEAN), has_hr (BOOLEAN), has_cadence (BOOLEAN), is_indoor (BOOLEAN), workout_type (CHECK: easy, long, workout, race, other), notes (TEXT), excluded_from_stats (BOOLEAN DEFAULT 0), primary_source_id (FK), stream_source_id (FK), summary_polyline (TEXT), duplicate_of_id (FK self), upstream_deleted_at (TEXT), created_at (TEXT), updated_at (TEXT).
   - `source_records`: id (PK), source (CHECK: strava, file_fit, file_gpx, file_tcx, apple_health), external_id (TEXT), activity_id (FK activities), status (CHECK: pending, mapped, duplicate, skipped, error), error (TEXT), raw_summary (JSON), raw_detail (JSON), file_path (TEXT), source_start_time_utc (TEXT), fetched_at (TEXT), mapper_version (INTEGER), job_id (FK jobs). UNIQUE(source, external_id).
   - `streams`: source_record_id (PK FK source_records), activity_id (FK activities), n_points (INTEGER), channels (TEXT JSON), data (BLOB gzip), codec_version (INTEGER).
   - `laps`: id (PK), activity_id (FK activities), kind (CHECK: device_lap, split_km), idx (INTEGER), start_offset_s (INTEGER), elapsed_s (INTEGER), moving_s (INTEGER), distance_m (REAL), avg_speed_ms (REAL), avg_hr (REAL), max_hr (REAL), avg_cadence_spm (REAL), elev_gain_m (REAL). UNIQUE(activity_id, kind, idx).
   - `best_efforts`: id (PK), activity_id (FK activities), distance_m (REAL), elapsed_s (INTEGER), start_offset_s (INTEGER), algo_version (INTEGER). UNIQUE(activity_id, distance_m).
   - `activity_metrics`: activity_id (PK FK activities), algo_version (INTEGER), computed_at (TEXT), zones_hash (TEXT), time_in_zones_s (JSON), efficiency_factor (REAL), pace_cv (REAL), is_steady (BOOLEAN), decoupling_pct (REAL), trimp (REAL), gps_suspect (BOOLEAN).
   - `tags` (id PK, name UNIQUE) and `activity_tags` (activity_id FK, tag_id FK, PK(activity_id, tag_id)).
   - `provider_accounts`: provider (PK), athlete_id (TEXT), access_token (TEXT), refresh_token (TEXT), expires_at (INTEGER), scopes (TEXT), status (TEXT), sync_cursor (INTEGER), last_sync_at (TEXT), last_reconcile_at (TEXT), updated_at (TEXT).
   - `jobs`: id (PK), kind (TEXT), status (CHECK: queued, running, done, failed), params (JSON), progress_done (INTEGER), progress_total (INTEGER), attempts (INTEGER DEFAULT 0), error (TEXT), not_before (TEXT), created_at (TEXT), started_at (TEXT), heartbeat_at (TEXT), finished_at (TEXT).
   - `settings`: key (PK TEXT), value (JSON).

3. Alembic initial migration in `alembic/versions/001_initial_schema.py`.
4. Unit tests in `tests/unit/test_models.py` verifying constraints, foreign keys, and unique checks.
</requirements>

<constraints>
- Strictly SI units. Absolutely NO users table or user_id column.
- Use ISO-8601 strings for SQLite timestamps.
- Complete implementation of all tables and constraints without cutting corners.
</constraints>

<verification>
Execute migration and test:
`alembic upgrade head` && `pytest tests/unit/test_models.py`
Ensure schema creates cleanly and tests pass.
</verification>
```
- **Verification:** `alembic upgrade head` && `pytest tests/unit/test_models.py`

---

### Task 1.2 — Gzip Stream Codec
- **Model:** Claude Sonnet 5.5
- **Effort:** Medium
- **Prompt:**
```xml
<task>
Implement Task 1.2: Gzip Stream Serialization Codec in app/domain/stream_codec.py.
</task>

<context>
Detailed streams (time, distance, hr, speed, lat, lng, altitude, cadence, power) are stored as gzipped JSON blobs in the SQLite `streams` table. Refer to docs/PLAN.md §7.2, §8.2, and CLAUDE.md.
</context>

<requirements>
1. Function `encode_stream(channels: dict[str, list[Any]]) -> bytes`:
   - Validate that all channel arrays share identical length.
   - Validate that `time` array is strictly non-decreasing.
   - Serialize dictionary to compact JSON (no whitespace) and compress with gzip (compression level 6).

2. Function `decode_stream(blob: bytes) -> dict[str, list[Any]]`:
   - Decompress gzip blob and deserialize JSON back into channel dictionary.

3. Unit Tests in tests/unit/test_stream_codec.py:
   - Roundtrip encoding and decoding fidelity with synthetic stream data.
   - Raise explicit ValueError if channel array lengths mismatch.
   - Handle empty streams gracefully.
</requirements>

<constraints>
- Pure functions only. Zero database dependencies.
- Concise implementation following project conventions.
</constraints>

<verification>
Run unit tests:
`pytest tests/unit/test_stream_codec.py`
</verification>
```
- **Verification:** `pytest tests/unit/test_stream_codec.py`

---

### Task 1.3 — Pure Strava Mapper
- **Model:** Claude Opus 5.5
- **Effort:** Medium
- **Prompt:**
```xml
<task>
Implement Task 1.3 of docs/PLAN.md §9.4: Pure Strava Mapper in app/ingest/strava_mapper.py.
</task>

<context>
Raw Strava payloads must be mapped into canonical domain models before database persistence. This is a pure mathematical and structural transformation with zero database or external I/O dependencies. Refer to docs/PLAN.md §9.4, §25 (M1-03), and CLAUDE.md.
</context>

<requirements>
1. Function `map_strava_activity(raw_detail: dict, raw_streams: dict | None) -> tuple[ActivityDraft, list[LapDraft], StreamDraft | None]`:
   - Sport filtering:
     - Accept: `Run`, `TrailRun`, `VirtualRun`.
     - If `VirtualRun` or `trainer: true`, set `is_indoor = True`.
     - For any other sport (Ride, Walk, Hike, Swim, etc.), return draft with `status = 'skipped'`.
   - SI Unit Conversion & Normalization:
     - Distance in meters (REAL).
     - Elapsed time & moving time in seconds (INTEGER).
     - Cadence: Strava `average_cadence` is RPM (one leg). Normalize by multiplying by 2 to yield total steps/min (spm).
     - Times & Timezone: Parse `start_date` to ISO UTC string. Extract clean IANA timezone string from Strava `timezone` field (e.g. `"(GMT+01:00) Europe/Rome"` -> `"Europe/Rome"`). Derive `local_date` (YYYY-MM-DD).
     - Workout type: Map Strava `workout_type`: 0/None -> 'easy', 1 -> 'race', 2 -> 'long', 3 -> 'workout'.
   - Stream mapping:
     - Map `latlng` array to separate `lat` and `lng` float channels.
     - `velocity_smooth` -> `speed` (m/s).
     - `heartrate` -> `hr`, `watts` -> `power`, `cadence` -> multiply samples by 2.
     - Retain standard channels (`time`, `distance`, `altitude`).
   - Laps mapping:
     - Extract device laps from `raw_detail.get('laps', [])` with `kind = 'device_lap'`.

2. Unit Tests in tests/unit/test_strava_mapper.py:
   - Verify cadence multiplication by 2.
   - Verify UTC parsing and IANA timezone extraction.
   - Verify non-run activities are skipped.
   - Use real or realistic fixtures from M0-08 spike.
</requirements>

<constraints>
- Pure deterministic functions only. No database calls, no network I/O.
- Do not stop mid-task to summarize progress; carry through all mapping rules and test implementations.
- Treat decisions in docs/PLAN.md as settled.
</constraints>

<verification>
Execute unit tests:
`pytest tests/unit/test_strava_mapper.py`
Verify all mapping rules and edge cases pass cleanly.
</verification>
```
- **Verification:** `pytest tests/unit/test_strava_mapper.py`

---

### Task 1.4 — Pure Metrics Engine (Splits, Zones, Best Efforts, EF, Steady)
- **Model:** Claude Opus 5.5
- **Effort:** Medium
- **Prompt:**
```xml
<task>
Implement Task 1.4 of docs/PLAN.md §13.2: Pure Metrics Computation Engine in app/metrics/engine.py.
</task>

<context>
The metrics engine calculates performance indicators, standard splits, best efforts, aerobic efficiency, and steady run classifications. It is pure mathematical logic (`ALGO_VERSION = 1`). Refer to docs/PLAN.md §13.2, §25 (M1-04), and CLAUDE.md.
</context>

<requirements>
Write pure, deterministic mathematical functions without I/O or DB dependencies:

1. `compute_km_splits(distance_stream: list[float], time_stream: list[int], hr_stream: list[float] | None, elev_stream: list[float] | None) -> list[SplitKm]`:
   - Linearly interpolate exact crossing timestamps and metrics at each 1,000m boundary.
   - Accurately calculate elapsed time, average speed (m/s), average HR, and elevation gain for each 1km split.
   - Handle partial final split (e.g. 5,420m produces five 1km splits + one 420m split).

2. `compute_best_efforts(distance_stream: list[float], time_stream: list[int], targets: list[float] = [400.0, 1000.0, 1609.34, 5000.0, 10000.0, 21097.5, 42195.0]) -> list[BestEffort]`:
   - Two-pointer sliding window over the continuous cumulative distance stream in O(N) time complexity.
   - Linearly interpolate fractional start and end points to find minimum elapsed time for each target distance.

3. `compute_time_in_zones(hr_stream: list[float], time_stream: list[int], zones: list[int]) -> list[int]`:
   - Calculate total seconds spent in HR zones 1 through 5.
   - Cap dt at 10 seconds between adjacent stream points to avoid inflating time during paused activities.

4. `compute_efficiency_factor(avg_speed_ms: float, avg_hr: float | None) -> float | None`:
   - Speed in meters/minute divided by average heart rate. Return None if HR is missing or zero.

5. `compute_steady_run(speed_stream: list[float], time_stream: list[int], is_indoor: bool, workout_type: str | None, threshold: float = 0.08) -> tuple[bool, float]`:
   - Compute moving coefficient of variation (CV = standard_deviation / mean) of pace over 60-second rolling windows.
   - Return `is_steady = True` if outdoor, duration >= 20 minutes (1,200s), and overall pace CV < threshold.

6. `detect_gps_suspect(speed_stream: list[float]) -> bool`:
   - Flag True if speed exceeds 7.0 m/s (~2:23/km) for more than 10 consecutive seconds.

7. Unit Tests in tests/unit/test_metrics.py:
   - Synthetic constant stream (3.0 m/s for 10,000m) verifying 5K best effort is exactly 1,666.67s.
   - Split summation matches overall distance and elapsed time within tolerance.
   - Edge cases: stream shorter than target distance, empty streams, missing HR.
</requirements>

<constraints>
- Pure functions using standard library and numpy/math. No DB dependencies.
- Do not stop partway to announce next steps; execute complete implementation and test suite.
</constraints>

<verification>
Run test suite:
`pytest tests/unit/test_metrics.py`
Verify coverage and mathematical accuracy.
</verification>
```
- **Verification:** `pytest tests/unit/test_metrics.py`

---

### Task 1.5 — Multi-Source Deduplication Logic
- **Model:** Claude Opus 5.5
- **Effort:** Medium
- **Prompt:**
```xml
<task>
Implement Task 1.5 of docs/PLAN.md §8.4: Cross-Source Deduplication in app/domain/dedup.py.
</task>

<context>
The platform must prevent duplicate activities and support enriching Strava runs with secondary files. Refer to docs/PLAN.md §8.4, §25 (M1-05), and CLAUDE.md.
</context>

<requirements>
1. Function `find_duplicate_candidate(new_start_utc: datetime, new_elapsed_s: int, new_dist_m: float | None, existing_activities: list[ActivitySummary]) -> tuple[DedupAction, int | None]`:
   - Candidate Matching Criteria:
     1. `|delta start_time| <= 120 seconds`
     2. Overlap of intervals `[start, start + elapsed]` >= 80%
     3. Distance within ±10% (when distance is present on both)
   - Decision Rules:
     - 0 matching candidates: `DedupAction.NEW_ACTIVITY`
     - 1 candidate from a DIFFERENT source: `DedupAction.ATTACH_TO_EXISTING` (enrichment)
     - 1 candidate from the SAME source: `DedupAction.FLAG_DUPLICATE` (sets `duplicate_of_id` and `excluded_from_stats = 1`)
     - Multiple candidates: `DedupAction.FLAG_DUPLICATE`

2. Unit Tests in tests/unit/test_dedup.py:
   - Same start time from different source -> `ATTACH_TO_EXISTING`.
   - Overlapping activities from same source -> `FLAG_DUPLICATE`.
   - Morning and evening runs on same date -> `NEW_ACTIVITY` (no false positive merge).
   - Time boundary tolerance tests (119s vs 121s).
</requirements>

<constraints>
- Pure deterministic function. No database calls.
- Carry implementation through to completion without pausing.
</constraints>

<verification>
Execute unit tests:
`pytest tests/unit/test_dedup.py`
</verification>
```
- **Verification:** `pytest tests/unit/test_dedup.py`

---

## Milestone 2: Resilient Strava Client & Job Worker

### Task 2.1 — SQLite Job Queue & Worker State Machine
- **Model:** Claude Opus 5.5
- **Effort:** Medium
- **Prompt:**
```xml
<task>
Implement Task 2.1 of docs/PLAN.md §8.5 & ADR-11: SQLite-backed Job Queue in app/worker/queue.py.
</task>

<context>
To keep operations at 0€ and single-user simplicity, background jobs are managed via SQLite in WAL mode. We avoid Celery/Redis. Concurrency requires atomic SQL state transitions. Refer to docs/PLAN.md §8.5, §24 (ADR-11), and CLAUDE.md.
</context>

<requirements>
1. Implement `JobQueue` class:
   - `enqueue(kind: str, params: dict, not_before: datetime | None = None) -> int`:
     Idempotent enqueue avoiding duplicate pending jobs of the same kind and parameters.
   - `claim_job() -> Job | None`:
     Atomically claim the next queued job using a single SQL UPDATE with subselect:
     `UPDATE jobs SET status='running', started_at=?, heartbeat_at=? WHERE id = (SELECT id FROM jobs WHERE status='queued' AND (not_before IS NULL OR not_before <= ?) ORDER BY id ASC LIMIT 1) RETURNING *`.
   - `heartbeat(job_id: int)`:
     Update `heartbeat_at = datetime.utcnow()` every 30 seconds during execution.
   - `finish_job(job_id: int, status: str, error: str | None = None)`:
     Mark job as `done` or `failed` with timestamp and error message.
   - `recover_stale_jobs()`:
     On worker startup, locate jobs in `running` with `heartbeat_at` older than 5 minutes and reset them to `queued` (or `failed` if attempts exceed max).

2. Unit Tests in tests/unit/test_job_queue.py:
   - Test atomic claiming prevents race conditions.
   - Test stale job recovery.
   - Test delayed execution via `not_before`.
</requirements>

<constraints>
- Pure SQLite implementation using SQLAlchemy or raw sqlite3 connection with WAL pragmas.
- Do not stop to announce progress; implement complete queue class and test suite.
</constraints>

<verification>
Run test suite:
`pytest tests/unit/test_job_queue.py`
</verification>
```
- **Verification:** `pytest tests/unit/test_job_queue.py`

---

### Task 2.2 — Strava Client with Rate Limiting & Token Rotation
- **Model:** Claude Opus 5.5
- **Effort:** Medium
- **Prompt:**
```xml
<task>
Implement Task 2.2 of docs/PLAN.md §9: Resilient Strava API Client in app/ingest/strava_client.py.
</task>

<context>
The Strava client must respect strict rate limits and manage automatic OAuth token refreshes. Crucially, Strava rotates refresh tokens on every refresh call: failing to atomically store both the new access and refresh token permanently breaks synchronization. Refer to docs/PLAN.md §9.2, §9.3, and CLAUDE.md.
</context>

<requirements>
1. Implement `StravaClient`:
   - Configurable base URL via `STRAVA_API_BASE` (default `https://www.strava.com/api/v3`, ready for `https://api-v3.strava.com`).
   - Rate Limit Tracking:
     - Maintain sliding window counters: max 90 requests per 15 minutes, max 900 requests per day.
     - Parse Strava `X-RateLimit-Usage` and `X-RateLimit-Limit` headers.
     - If approaching limit or on HTTP 429, compute sleep duration until next window reset (:00, :15, :30, :45 or midnight UTC) and raise `RateLimitWaitException(resume_at)`.
   - Token Rotation & Refresh:
     - Automatically check token expiration. If expiring within 5 minutes, call `POST /oauth/token` with `grant_type=refresh_token`.
     - **CRITICAL**: Persist BOTH the new `access_token` and the rotated `refresh_token` in `provider_accounts` table within a single atomic database transaction before making subsequent API calls.
   - Client API Methods:
     - `get_athlete_activities(after: int | None, before: int | None, page: int, per_page: int) -> list[dict]`
     - `get_activity_detail(activity_id: int) -> dict`
     - `get_activity_streams(activity_id: int, keys: list[str]) -> dict`
     - `revoke_token(token: str) -> bool`

2. Integration Tests in tests/integration/test_strava_client.py using `respx`:
   - Test automatic token refresh and atomic DB persistence of rotated refresh token.
   - Test 429 handling and rate limit backoff computation.
   - Test configurable base URL.
</requirements>

<constraints>
- Handle rate limits deterministically without unhandled crashes.
- Carry work through until all methods and respx tests are verified.
</constraints>

<verification>
Execute integration tests:
`pytest tests/integration/test_strava_client.py`
</verification>
```
- **Verification:** `pytest tests/integration/test_strava_client.py`

---

### Task 2.3 — Background Worker & Sync Pipeline
- **Model:** Claude Sonnet 5.5
- **Effort:** High
- **Prompt:**
```xml
<task>
Implement Task 2.3 of docs/PLAN.md §8 & §9.3: Ingestion Worker Pipeline in app/worker/runner.py and app/worker/__main__.py.
</task>

<context>
The worker executes queued synchronization jobs, coordinates fetching, mapping, deduplication, and metrics computation, and commits each activity atomically. Refer to docs/PLAN.md §8.1, §8.3, §9.3, and CLAUDE.md.
</context>

<requirements>
1. Implement Job Handlers in app/worker/runner.py:
   - `run_strava_backfill(job)`: Paginate backwards through all historical athlete activities. For each run: fetch detail + streams, map, dedup, compute metrics, and commit in a single atomic DB transaction per activity. Update job progress.
   - `run_strava_sync(job)`: Fetch activities with `after = last_sync_cursor - 1h`. Also inspect activities from the past 30 days to detect title/distance changes. Upsert accordingly. Ping healthchecks.io sync URL upon success.
   - `run_strava_reconcile(job)`: Weekly reconciliation check of all external IDs. If deleted on Strava, set `upstream_deleted_at = now` and `excluded_from_stats = 1` (never hard delete).
   - `run_recompute_metrics(job)`: Recompute metrics across all activities when HR zones or settings change.

2. In-Process Periodic Scheduler:
   - Schedule `strava_sync` every 30 minutes.
   - Schedule `strava_reconcile` every 7 days.

3. Worker Main Loop in app/worker/__main__.py:
   - Recover stale jobs on startup.
   - Continuously claim and process jobs, update heartbeats, and handle `RateLimitWaitException` by updating `not_before` on the job.

4. Integration Tests in tests/integration/test_sync_pipeline.py:
   - Test backfill job processes mock activities and updates database.
   - Test reconciliation marks upstream deletions without deleting local records.
</requirements>

<constraints>
- Single atomic transaction per activity: no half-imported runs.
- Keep changes scoped strictly to worker runner, main loop, and tests.
- Run tests and verify before reporting done.
</constraints>

<verification>
Run pipeline tests:
`pytest tests/integration/test_sync_pipeline.py`
</verification>
```
- **Verification:** `pytest tests/integration/test_sync_pipeline.py`

---

## Milestone 3: FastAPI REST Endpoints

### Task 3.1 — Activities & Streams Endpoints
- **Model:** Claude Sonnet 5.5
- **Effort:** High
- **Prompt:**
```xml
<task>
Implement Task 3.1 of docs/PLAN.md §11: Activities API Routers in app/api/activities.py.
</task>

<context>
REST endpoints provide activity listings, detailed views with streams, similarity comparisons, and local metadata editing. All units internally SI; pace formatted dynamically on client. Refer to docs/PLAN.md §11.1, §25 (M3-01, M3-02, M3-03), and CLAUDE.md.
</context>

<requirements>
1. Endpoints in app/api/activities.py:
   - `GET /api/activities`:
     - Query parameters: `from_date`, `to_date`, `dist_min`, `dist_max`, `dur_min`, `dur_max`, `pace_min`, `pace_max`, `hr_min`, `hr_max`, `type[]`, `workout_type[]`, `tag[]`, `source[]`, `q`, `include_excluded`, `sort`, `order`, `page`, `page_size`.
     - Exclude `excluded_from_stats` and `duplicate_of_id` by default.
     - Return `{items: list[ActivitySummary], total: int, aggregate: {count: int, distance_m: float, moving_s: int, weighted_pace_s_per_km: float}}`. Weighted pace must be `total_moving_s / (total_dist_m / 1000)`.
   - `GET /api/activities/{id}`:
     - Return full detail: `{activity, metrics, laps, splits, best_efforts, sources, tags, duplicate_candidates}`.
     - Mark best efforts with PR flag if they represent the all-time PR at the time of the activity.
   - `GET /api/activities/{id}/streams`:
     - Return gzipped channel dictionary with requested streams (`time`, `distance`, `hr`, `speed`, `lat`, `lng`, `altitude`, `cadence`, `power`).
   - `GET /api/activities/{id}/similar`:
     - Return last 5 runs of the same sport type within ±15% distance, including metric deltas.
   - `PATCH /api/activities/{id}`:
     - Allow updating local fields (`notes`, `workout_type`, `tags`, `excluded_from_stats`). Ensure sync runs do not overwrite these fields.
   - `GET /api/tags`: List all distinct tags.

2. Integration Tests in tests/integration/test_activities_api.py:
   - Test filtering by distance, date range, and tags.
   - Test weighted pace aggregation.
   - Test PATCH endpoint updates local fields without affecting sync fields.
</requirements>

<constraints>
- Strict SI units internally. Return 404 for missing activities.
- Run tests and verify output before reporting completion.
</constraints>

<verification>
Execute API tests:
`pytest tests/integration/test_activities_api.py`
</verification>
```
- **Verification:** `pytest tests/integration/test_activities_api.py`

---

### Task 3.2 — Statistics & Dashboard Aggregation Endpoints
- **Model:** Claude Sonnet 5.5
- **Effort:** High
- **Prompt:**
```xml
<task>
Implement Task 3.2 of docs/PLAN.md §11 & §13: Stats & Analytics Endpoints in app/api/stats.py.
</task>

<context>
Analytics queries compute volume aggregations, trends, and records across historical runs. Aggregations run directly in SQLite (<300ms p95). Refer to docs/PLAN.md §11.1, §13.1, §13.3, §25 (M3-04, M3-05), and CLAUDE.md.
</context>

<requirements>
1. Endpoints in app/api/stats.py (accepting `from_date`, `to_date`, `types[]`, `include_indoor`):
   - `GET /api/stats/summary`: Current period vs equal-length previous period (km, moving_s, run_count, avg_weekly_km, longest_run_m, weighted_pace).
   - `GET /api/stats/volume?bucket=week|month`: Time series of volume, moving time, elevation, and partial bucket flag for current period.
   - `GET /api/stats/calendar?year=YYYY`: Daily distance array for calendar heatmap.
   - `GET /api/stats/distribution?field=distance|duration|pace`: Histogram buckets and counts.
   - `GET /api/stats/zones?bucket=week`: Seconds spent in Z1-Z5 per bucket.
   - `GET /api/stats/trends?metric=pace|ef|hr`: Steady runs points, 28-day rolling median, and Theil-Sen slope calculation (median of pairwise slopes), returning observation count `n` (suppressed if `n < 8`).
   - `GET /api/stats/pace-hr`: Scatter points (pace, hr, date) for steady runs.
   - `GET /api/stats/top-weeks?limit=10`: Top weeks ranked by total distance.
   - `GET /api/records`: Current PRs for 400m, 1K, 1mi, 5K, 10K, Half Marathon, Marathon, plus historical progression.

2. Integration Tests in tests/integration/test_stats_api.py:
   - Test ISO week boundary calculation with local timezone.
   - Test weighted pace computation.
   - Verify Theil-Sen trend suppression when `n < 8`.
</requirements>

<constraints>
- Weighted pace must always be `total_time / total_distance`, never average of paces.
- Exercise code with pytest before declaring completion.
</constraints>

<verification>
Run stats API test suite:
`pytest tests/integration/test_stats_api.py`
</verification>
```
- **Verification:** `pytest tests/integration/test_stats_api.py`

---

### Task 3.3 — Sync, Settings & OpenAPI TypeScript Codegen
- **Model:** Claude Sonnet 5.5
- **Effort:** Medium
- **Prompt:**
```xml
<task>
Implement Task 3.3 of docs/PLAN.md §11: Sync, Settings Endpoints and OpenAPI Type Generation.
</task>

<context>
Management endpoints for Strava connection status, manual sync trigger, job inspection, and heart rate zone settings. Refer to docs/PLAN.md §11.1, §25 (M3-06, M4-04, M4-05), and CLAUDE.md.
</context>

<requirements>
1. Endpoints in app/api/sync.py and app/api/settings.py:
   - `GET /api/strava/status`: Return connection status, athlete_id, last_sync_at, and rate usage.
   - `POST /api/sync`: Enqueue `strava_sync` job; return `{job_id}`. Prevent duplicate queueing if a sync job is already queued.
   - `GET /api/jobs`: List recent jobs with status, progress, error, and timestamps.
   - `GET /api/jobs/{id}`: Return detailed job state.
   - `POST /api/source-records/{id}/retry`: Re-enqueue failed source record for ingestion.
   - `GET /api/settings` and `PUT /api/settings`: Read/update `hr_max`, `hr_rest`, `hr_zones` (validated strictly ascending), `steady_cv_threshold`. PUT triggers a background `recompute_metrics` job if zones change.

2. Frontend Codegen Script in web/package.json:
   - Add `"codegen": "openapi-typescript http://127.0.0.1:8000/openapi.json -o src/api/schema.d.ts"`.

3. Tests in tests/integration/test_settings_api.py:
   - Test zone validation rejects non-ascending values.
   - Test updating zones triggers recomputation job.
</requirements>

<constraints>
- Keep changes concise. Verify endpoints with pytest.
</constraints>

<verification>
Execute integration tests:
`pytest tests/integration/test_settings_api.py`
</verification>
```
- **Verification:** `pytest tests/integration/test_settings_api.py`

---

## Milestone 4: Frontend Base & Activity Views

### Task 4.1 — Dark Shell, Navigation & Formatting Utilities
- **Model:** Claude Sonnet 5.5
- **Effort:** Medium
- **Prompt:**
```xml
<task>
Implement Task 4.1 of docs/PLAN.md §12: UI Foundation, Navigation, and Formatters in web/.
</task>

<context>
Single-user running analytics dashboard. Dark-only theme, responsive down to 375px without horizontal scroll. Numbers must use monospace tabular figures. Refer to docs/PLAN.md §12.1, §12.2, §12.5, and CLAUDE.md.
</context>

<requirements>
1. Styling & Theme Tokens:
   - Strictly dark-only palette: Background `#0a0a0c`, panel surface `#121216`, border `#22222a`, accent blue `#3b82f6`.
   - Semantic series colors: Pace (`#3b82f6`), HR (`#ef4444`), Elevation (`#6b7280`), Cadence (`#a855f7`), Power (`#f59e0b`).
   - Typography: Class `font-mono tabular-nums` on all metrics and figures.

2. Responsive Navigation Shell:
   - Desktop: Top bar with logo, nav links (Dashboard, Activities, Records, Sync, Settings), and connection indicator.
   - Mobile (<768px): Bottom navigation bar with 4 primary destinations (Dashboard, Activities, Records, Sync) with touch targets >= 44px. No horizontal scroll down to 375px.

3. Formatters in web/src/utils/formatters.ts:
   - `formatPace(secondsPerKm: number | null): string` (e.g. 312 -> "5:12 /km").
   - `formatDistance(meters: number): string` (e.g. 10250 -> "10.25 km").
   - `formatDuration(seconds: number): string` (e.g. 3665 -> "1h 01m 05s").
   - `formatDate(isoUtc: string, timezone: string): string`.
   - Write Vitest tests in `web/src/utils/formatters.test.ts`.

4. TanStack Query & API Client:
   - Configured API client adding `X-Corsa: 1` header to mutating HTTP requests (`POST`, `PUT`, `PATCH`, `DELETE`).
</requirements>

<constraints>
- Pure dark theme only. No light mode toggle.
- Tabular figures on all numerical values.
- Verify formatters pass tests before finishing.
</constraints>

<verification>
Run frontend test suite:
`npm --prefix web run test`
</verification>
```
- **Verification:** `npm --prefix web run test`

---

### Task 4.2 — Activities List Page & Aggregated Filter Bar
- **Model:** Claude Sonnet 5.5
- **Effort:** Medium
- **Prompt:**
```xml
<task>
Implement Task 4.2 of docs/PLAN.md §12.4: Activities List Page in web/src/pages/ActivitiesPage.tsx.
</task>

<context>
A dense, high-information activity list with rich filtering synced to URL search parameters. Refer to docs/PLAN.md §12.4, §25 (M4-02), and CLAUDE.md.
</context>

<requirements>
1. Filter Bar:
   - Date range presets: 4W, 12W, 6M, YTD, 1Y, All, plus custom date range picker.
   - Numeric inputs for Distance min/max, Pace min/max, HR min/max.
   - Multi-selects for sport type and workout type.
   - Text search input with debouncing.
   - Synchronize all filter parameters with URL search params (bookmarkable and browser history friendly).

2. Aggregated Header Strip:
   - Live summary line above table updating with filter selection: e.g. "23 runs · 187.4 km · 16h 42m · 5:21 /km".

3. Activities Table:
   - Columns: Date, Name, Type, Distance, Duration, Pace, Avg HR, Elevation Gain, Workout Type.
   - Dense desktop table layout, responsive stacked cards on mobile (<768px).
   - Clickable rows navigating to `/activities/:id`.
   - Server-side pagination (50 items/page).
</requirements>

<constraints>
- Strictly tabular-nums for numerical metrics.
- Keep layout dense and functional; avoid frivolous card padding.
- Verify TypeScript types cleanly.
</constraints>

<verification>
Execute type check:
`npm --prefix web run typecheck`
</verification>
```
- **Verification:** `npm --prefix web run typecheck`

---

### Task 4.3 — Activity Detail View (MapLibre + Synchronized ECharts)
- **Model:** Claude Opus 5.5
- **Effort:** Medium
- **Prompt:**
```xml
<task>
Implement Task 4.3 of docs/PLAN.md §12.3: Activity Detail Page in web/src/pages/ActivityDetailPage.tsx.
</task>

<context>
A comprehensive activity breakdown featuring synchronized MapLibre GL map and Apache ECharts telemetry charts. Refer to docs/PLAN.md §12.3, §25 (M4-03), and CLAUDE.md.
</context>

<requirements>
1. Layout Structure:
   - Header: Activity name, local date/time, editable workout_type dropdown, link to upstream Strava activity.
   - Two-column Stat Grid (tabular figures with deltas vs similar runs):
     - Distance, Moving Time, Total Time, Weighted Pace, Best 1km.
     - Avg HR, Max HR, Aerobic Efficiency Factor (EF).
     - Elevation Gain/Loss.
     - Epistemic markers: Power (`est.`), Calories (`est.`).
   - Interactive Section (Desktop side-by-side >= 1280px):
     - Left (60%): MapLibre GL map with OpenFreeMap Dark tiles (`https://tiles.openfreemap.org/styles/dark`). Track color-coded by Pace or HR. Start and finish markers.
     - Right (40%): KM Splits table with horizontal pace delta bars.
   - Synchronized Apache ECharts (Pace, Heart Rate with HR zone background bands, Elevation area):
     - Shared X-axis with toggle between Distance and Time.
     - **CRITICAL**: Invert Pace Y-axis (faster pace = higher point), clamped at 10:00/km.
     - Synchronized cursor and tooltip: hovering over any chart moves the marker on the map and highlights the matching KM split row.
   - HR Zone distribution horizontal segmented bar with times and percentages.
   - Best Efforts table (400m, 1k, 1mi, 5k, 10k, 21.1k, 42.2k) with PR badges.
   - Inline editor for Notes and Tags.
   - Collapsible "Sources & Raw Data" inspector.
</requirements>

<constraints>
- Negative Frontend Constraints: Do not use light or cream backgrounds, rounded pill buttons, decorative gradient text, or generic card grids. Strictly adhere to dark theme (#0a0a0c background, #121216 panel, #22222a border) with `font-mono tabular-nums` for all metrics.
- Synchronize cursor across charts without frame drops. Responsive down to 375px.
- Do not stop partway to announce next steps; execute complete implementation and verify frontend build.
</constraints>

<verification>
Build the frontend project:
`npm --prefix web run build`
Verify zero build errors or TypeScript violations.
</verification>
```
- **Verification:** `npm --prefix web run build`

---

### Task 4.4 — Sync & Settings Pages
- **Model:** Claude Sonnet 5.5
- **Effort:** Medium
- **Prompt:**
```xml
<task>
Implement Task 4.4 of docs/PLAN.md §11 & §12: Sync & Settings Pages.
</task>

<context>
User interface for managing Strava integration, inspecting background jobs, and adjusting heart rate zones. Refer to docs/PLAN.md §11.1, §12.5, and CLAUDE.md.
</context>

<requirements>
1. Sync Page (web/src/pages/SyncPage.tsx):
   - Strava status badge (Connected / Disconnected / Reauth Required), Athlete ID, last sync time, rate limit usage bar.
   - Action buttons: "Connect Strava" (redirects), "Disconnect", and "Sync Now" (triggers `POST /api/sync`).
   - Recent Jobs table showing kind, status, progress, timestamps, and error messages.
   - Failed source records table with "Retry" action button.

2. Settings Page (web/src/pages/SettingsPage.tsx):
   - Heart Rate Settings: Max HR, Resting HR, Zone boundaries (Z1 to Z5 bpm thresholds) with client-side ascending validation.
   - Steady run CV threshold slider (default 0.08).
   - Warning banner: "Modifying HR zones will trigger a background recomputation of all activity metrics."
   - Save button sending `PUT /api/settings` with confirmation toast.
</requirements>

<constraints>
- Follow dark theme tokens and monospace tabular figures.
- Verify building the web project cleanly.
</constraints>

<verification>
Execute frontend build:
`npm --prefix web run build`
</verification>
```
- **Verification:** `npm --prefix web run build`

---

## Milestone 5: Analytics Dashboard & Records

### Task 5.1 — Core Dashboard Visualizations (D1 to D6)
- **Model:** Claude Opus 5.5
- **Effort:** Medium
- **Prompt:**
```xml
<task>
Implement Task 5.1 of docs/PLAN.md §13: Dashboard Analytics (D1 - D6) in web/src/pages/DashboardPage.tsx.
</task>

<context>
The core dashboard presents aggregated volume, consistency, pace evolution, and aerobic efficiency trends. Refer to docs/PLAN.md §13.1, §13.3, §25 (M5-01, M5-02, M5-03), and CLAUDE.md.
</context>

<requirements>
1. Global Controls:
   - Period selector: 4W, 12W, 6M, YTD, 1Y, All, Custom (persisted in URL search params).
   - Granularity toggle: Week / Month.

2. Visualizations using Apache ECharts:
   - **D1 Summary Strip**: Dense text line displaying Total km, Moving Time, Runs count, Avg km/wk, Longest run, Weighted pace, with signed delta (+/-) vs previous identical period.
   - **D2 Volume Chart**: Stacked/single bar chart for weekly/monthly km (dashed bar for current partial bucket) + 4-week moving average line. Toggle km / time / elevation / runs.
   - **D3 Consistency Heatmap**: 1-year calendar heatmap (GitHub-style, daily km).
   - **D4 Weekly Long Run**: Line chart showing weekly maximum run distance + percentage of total weekly volume.
   - **D5 Pace Evolution**: Scatter plot of steady runs with 28-day rolling median line and inverted pace axis. Show observation count (e.g. `n=24`).
   - **D6 Aerobic Efficiency (EF) Trend**: Scatter of steady run EF + 28-day median + Theil-Sen slope line (e.g. "+2.1% in 12 weeks, n=31"). Suppress slope line if `n < 8` with explanatory label.
</requirements>

<constraints>
- Negative Frontend Constraints: Do not use light backgrounds, generic cards, pill buttons, or decorative gradient text. Strictly follow dark palette (#0a0a0c background, #121216 panel, #22222a borders).
- Ensure all numbers use `font-mono tabular-nums`. Invert pace Y-axis.
- Carry implementation through to completion without pausing.
</constraints>

<verification>
Build the frontend application:
`npm --prefix web run build`
Verify clean build without type errors.
</verification>
```
- **Verification:** `npm --prefix web run build`

---

### Task 5.2 — Extended Analytics (D7 to D11) & Records Page
- **Model:** Claude Sonnet 5.5
- **Effort:** Medium
- **Prompt:**
```xml
<task>
Implement Task 5.2 of docs/PLAN.md §13: Dashboard Charts (D7-D11) and Records Page.
</task>

<context>
Extended analytics charts and dedicated personal record tracking with progression timelines. Refer to docs/PLAN.md §13.1, §13.4, §25 (M5-04, M5-05), and CLAUDE.md.
</context>

<requirements>
1. Dashboard Charts in web/src/pages/DashboardPage.tsx:
   - **D7 Pace vs HR**: Scatter plot for steady runs (X: Pace inverted, Y: HR). Point color mapped to date (older = faded, newer = bright).
   - **D8 Zone Distribution**: Stacked weekly bar chart of time spent in Z1-Z5 + overall percentage breakdown.
   - **D9 Distance Distribution**: Histogram of run counts across distance buckets (0-5k, 5-8k, 8-12k, 12-16k, 16-21k, 21k+).
   - **D10 Best Efforts Progression**: Scatter of best efforts for 1K, 5K, 10K, Half Marathon with stepped "PR over time" line.
   - **D11 Top Weeks**: Compact table of the top 10 highest volume weeks (clickable to filtered view).

2. Records Page in web/src/pages/RecordsPage.tsx:
   - Cards/Table for 400m, 1K, 1mi, 5K, 10K, Half Marathon, Marathon PRs.
   - Each record card shows: Time, Pace, Date, link to Activity, and historical progression list.
</requirements>

<constraints>
- Strictly dark theme with tabular figures on all numbers.
- Verify frontend compiles without warnings or errors.
</constraints>

<verification>
Execute frontend build:
`npm --prefix web run build`
</verification>
```
- **Verification:** `npm --prefix web run build`

---

## Milestone 6: Hardening, E2E & Operations

### Task 6.1 — Playwright E2E Tests & Performance Validation
- **Model:** Claude Sonnet 5.5
- **Effort:** Medium
- **Prompt:**
```xml
<task>
Implement Task 6.1 of docs/PLAN.md §18: Playwright E2E Tests and Performance Benchmark.
</task>

<context>
Automated end-to-end verification covering the 3 critical user journeys and performance validation (<300ms p95 on 1,000 activities). Refer to docs/PLAN.md §18, §25 (M6-01, M6-02), and CLAUDE.md.
</context>

<requirements>
1. Synthetic Data Seed Script in scripts/seed_demo_data.py:
   - Generates 100 realistic runs with GPS tracks, HR, and gzipped streams for local development and testing.

2. Playwright E2E Tests in tests/e2e/:
   - Flow 1: Dashboard loads, period change updates volume chart without error.
   - Flow 2: Activities page -> filter by distance -> click run -> verify map and synchronized ECharts render properly.
   - Flow 3: Activity detail page -> edit notes and workout_type -> reload -> verify updates persisted.

3. Performance Benchmark Script in scripts/benchmark_stats.py:
   - Seeds 1,000 synthetic activities into test database.
   - Measures p95 latency for `GET /api/stats/summary` and `GET /api/stats/volume`. Asserts p95 < 300ms.
</requirements>

<constraints>
- 3 critical user flows only; no snapshot bloat.
- Verify tests execute cleanly.
</constraints>

<verification>
Run Playwright tests:
`npx playwright test`
</verification>
```
- **Verification:** `npx playwright test`

---

### Task 6.2 — Backup, Deployment Scripts & RUNBOOK
- **Model:** Claude Sonnet 5.5
- **Effort:** Medium
- **Prompt:**
```xml
<task>
Implement Task 6.2 of docs/PLAN.md §16, §17 & §19: Automation Scripts and Operational RUNBOOK.
</task>

<context>
Operations, automated off-site backups via restic to OCI Object Storage, and zero-downtime deployment script on Oracle Cloud ARM64. Refer to docs/PLAN.md §16, §17, §19, and CLAUDE.md.
</context>

<requirements>
1. scripts/deploy.sh:
   - Deployment sequence: `git pull`, `docker compose build`, hot SQLite backup (`sqlite3 /data/corsa.db ".backup /data/pre-deploy-$(date +%s).db"`), `docker compose up -d`, and curl check against `/healthz`.

2. scripts/backup.sh:
   - Nightly backup script: `sqlite3 /data/corsa.db ".backup /tmp/backup.db"`, run `restic backup /tmp/backup.db` to OCI Object Storage bucket, prune snapshots, verify disk usage (<80%), and ping healthchecks.io.

3. scripts/restore.sh:
   - Restore script retrieving latest restic snapshot to clean machine.

4. docs/RUNBOOK.md:
   - Step-by-step setup guide for fresh Ubuntu ARM64 VM (Docker, Tailscale, systemd).
   - Disaster recovery drill procedures.
   - Secret rotation steps.
</requirements>

<constraints>
- Robust bash scripting (`set -euo pipefail`).
- Direct, actionable operations guide.
</constraints>

<verification>
Verify shell script syntax:
`bash -n scripts/deploy.sh` && `bash -n scripts/backup.sh` && `bash -n scripts/restore.sh`
</verification>
```
- **Verification:** `bash -n scripts/deploy.sh` && `bash -n scripts/backup.sh` && `bash -n scripts/restore.sh`
