from typing import Any, cast

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import Engine, select

from app.api.activities import Db
from app.api.strava_auth import PROVIDER
from app.domain.models import Job, ProviderAccount, SourceRecord
from app.ingest.strava_client import LIMIT_15M, LIMIT_DAY
from app.worker.queue import JobQueue

router = APIRouter(prefix="/api")


def _job(j: Job) -> dict[str, Any]:
    return {
        "id": j.id,
        "kind": j.kind,
        "status": j.status,
        "params": j.params,
        "progress_done": j.progress_done,
        "progress_total": j.progress_total,
        "attempts": j.attempts,
        "error": j.error,
        "not_before": j.not_before,
        "created_at": j.created_at,
        "started_at": j.started_at,
        "heartbeat_at": j.heartbeat_at,
        "finished_at": j.finished_at,
    }


def queue(db: Db) -> JobQueue:
    db.commit()  # release the read txn so the queue's own write transaction isn't blocked
    return JobQueue(cast(Engine, db.get_bind()))


@router.get("/strava/status")
def strava_status(db: Db) -> dict[str, Any]:
    acc = db.get(ProviderAccount, PROVIDER)
    # ponytail: usage counters live in the worker process; expose limits only until persisted
    rate = {"used_15m": None, "used_day": None, "limit_15m": LIMIT_15M, "limit_day": LIMIT_DAY}
    return {
        "connected": bool(acc and acc.refresh_token),
        "athlete_id": acc.athlete_id if acc else None,
        "scopes": acc.scopes.split(",") if acc and acc.scopes else [],
        "status": acc.status if acc else None,
        "last_sync_at": acc.last_sync_at if acc else None,
        "rate_usage": rate,
    }


@router.post("/sync")
def sync(db: Db) -> dict[str, int]:
    return {"job_id": queue(db).enqueue("strava_sync", {})}  # dedups queued/running


@router.get("/jobs")
def jobs(
    db: Db, status: str | None = None, limit: int = Query(50, ge=1, le=200)
) -> list[dict[str, Any]]:
    q = select(Job).order_by(Job.id.desc()).limit(limit)
    if status:
        q = q.where(Job.status == status)
    return [_job(j) for j in db.scalars(q)]


@router.get("/jobs/{job_id}")
def job_detail(job_id: int, db: Db) -> dict[str, Any]:
    j = db.get(Job, job_id)
    if j is None:
        raise HTTPException(404, "job not found")
    failed = db.execute(
        select(SourceRecord.id, SourceRecord.external_id, SourceRecord.error).where(
            SourceRecord.job_id == job_id, SourceRecord.status == "error"
        )
    ).all()
    return {
        "job": _job(j),
        "failed_records": [{"id": i, "external_id": e, "error": err} for i, e, err in failed],
    }


@router.post("/source-records/{rec_id}/retry")
def retry_record(rec_id: int, db: Db) -> dict[str, int]:
    rec = db.get(SourceRecord, rec_id)
    if rec is None:
        raise HTTPException(404, "source record not found")
    if rec.status != "error" or rec.source != PROVIDER:
        raise HTTPException(409, "only failed strava records can be retried")
    # ponytail: backfill retries every errored record, not just this one; per-record job later
    return {"job_id": queue(db).enqueue("strava_backfill", {})}
