from typing import Any, cast

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import Engine, select

from app.api.activities import Db
from app.core.config import get_settings
from app.domain.models import Job, SourceRecord
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


@router.get("/intervals/status")
def intervals_status() -> dict[str, bool]:
    return {"enabled": bool(get_settings().INTERVALS_API_KEY)}


@router.post("/intervals/sync")
def intervals_sync(db: Db) -> dict[str, int]:
    if not get_settings().INTERVALS_API_KEY:
        raise HTTPException(409, "INTERVALS_API_KEY not configured")
    db.commit()  # release the read txn so the queue's write isn't blocked
    return {"job_id": JobQueue(cast(Engine, db.get_bind())).enqueue("intervals_sync", {})}


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
