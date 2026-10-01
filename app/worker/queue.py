"""SQLite job queue (PLAN §8.5, ADR-11). Every state transition is one atomic SQL statement."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Engine, text

from app.domain.models import Job

HEARTBEAT_INTERVAL_S = 30
STALE_AFTER = timedelta(minutes=5)
MAX_ATTEMPTS = 3


def _iso(dt: datetime) -> str:
    # Same format as models.utcnow_iso so string comparison == time comparison.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def _now() -> str:
    return _iso(datetime.now(UTC))


def _row_to_job(row: Any) -> Job:
    d = dict(row._mapping)
    if isinstance(d["params"], str):
        d["params"] = json.loads(d["params"])
    return Job(**d)


class JobQueue:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def enqueue(self, kind: str, params: dict[str, Any], not_before: datetime | None = None) -> int:
        """Insert unless an identical queued/running job exists; return the job id either way."""
        p = json.dumps(params, sort_keys=True)
        with self.engine.begin() as c:
            row = c.execute(
                text(
                    "INSERT INTO jobs (kind, status, params, attempts, not_before, created_at) "
                    "SELECT :kind, 'queued', :p, 0, :nb, :now WHERE NOT EXISTS ("
                    " SELECT 1 FROM jobs WHERE kind=:kind AND params=:p"
                    " AND status IN ('queued','running')) RETURNING id"
                ),
                {"kind": kind, "p": p, "nb": not_before and _iso(not_before), "now": _now()},
            ).first()
            if row:
                return int(row[0])
            existing: int = c.execute(
                text(
                    "SELECT id FROM jobs WHERE kind=:kind AND params=:p"
                    " AND status IN ('queued','running') ORDER BY id LIMIT 1"
                ),
                {"kind": kind, "p": p},
            ).scalar_one()
            return int(existing)

    def claim_job(self) -> Job | None:
        now = _now()
        with self.engine.begin() as c:
            row = c.execute(
                text(
                    "UPDATE jobs SET status='running', started_at=:now, heartbeat_at=:now,"
                    " attempts=attempts+1 WHERE id = (SELECT id FROM jobs WHERE status='queued'"
                    " AND (not_before IS NULL OR not_before <= :now) ORDER BY id ASC LIMIT 1)"
                    " AND status='queued' RETURNING *"
                ),
                {"now": now},
            ).first()
        return _row_to_job(row) if row else None

    def heartbeat(self, job_id: int) -> None:
        """Caller invokes every HEARTBEAT_INTERVAL_S while the job runs."""
        with self.engine.begin() as c:
            c.execute(
                text("UPDATE jobs SET heartbeat_at=:now WHERE id=:id AND status='running'"),
                {"now": _now(), "id": job_id},
            )

    def progress(self, job_id: int, done: int, total: int) -> None:
        """Progress tick; doubles as heartbeat."""
        with self.engine.begin() as c:
            c.execute(
                text(
                    "UPDATE jobs SET progress_done=:d, progress_total=:t, heartbeat_at=:now"
                    " WHERE id=:id AND status='running'"
                ),
                {"d": done, "t": total, "now": _now(), "id": job_id},
            )

    def requeue(self, job_id: int, not_before: datetime, refund_attempt: bool = False) -> None:
        """Running -> queued, not claimable before `not_before` (rate-limit wait / retry backoff)."""
        with self.engine.begin() as c:
            c.execute(
                text(
                    "UPDATE jobs SET status='queued', not_before=:nb, started_at=NULL,"
                    " heartbeat_at=NULL, attempts=attempts-:r WHERE id=:id AND status='running'"
                ),
                {"nb": _iso(not_before), "r": int(refund_attempt), "id": job_id},
            )

    def finish_job(self, job_id: int, status: str, error: str | None = None) -> None:
        if status not in ("done", "failed"):
            raise ValueError(f"invalid final status: {status}")
        with self.engine.begin() as c:
            c.execute(
                text("UPDATE jobs SET status=:s, error=:e, finished_at=:now WHERE id=:id"),
                {"s": status, "e": error, "now": _now(), "id": job_id},
            )

    def recover_stale_jobs(self) -> int:
        """Reset running jobs with a heartbeat older than STALE_AFTER. Returns rows touched."""
        cutoff = _iso(datetime.now(UTC) - STALE_AFTER)
        with self.engine.begin() as c:
            res = c.execute(
                text(
                    "UPDATE jobs SET"
                    " status = CASE WHEN attempts >= :max THEN 'failed' ELSE 'queued' END,"
                    " error = CASE WHEN attempts >= :max THEN 'stale: max attempts exceeded'"
                    " ELSE error END,"
                    " finished_at = CASE WHEN attempts >= :max THEN :now ELSE NULL END,"
                    " started_at = NULL, heartbeat_at = NULL"
                    " WHERE status='running' AND (heartbeat_at IS NULL OR heartbeat_at < :cutoff)"
                ),
                {"max": MAX_ATTEMPTS, "now": _now(), "cutoff": cutoff},
            )
            return int(res.rowcount)
