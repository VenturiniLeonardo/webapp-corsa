from typing import Any, cast
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import Engine

from app.api.activities import Db
from app.api.ai import queue_auto_analysis
from app.ingest.files import Rejected, import_upload
from app.ingest.hae import import_workouts

router = APIRouter(prefix="/api")


@router.post("/imports/health-auto-export")
def health_auto_export(body: dict[str, Any], db: Db) -> dict[str, Any]:
    """Health Auto Export REST automation: {"data": {"workouts": [...]}}. Idempotent per id."""
    workouts = (body.get("data") or {}).get("workouts")
    if not isinstance(workouts, list):
        raise HTTPException(422, "expected data.workouts[]")
    db.commit()  # release the read txn: the import writes through its own sessions
    engine = cast(Engine, db.get_bind())
    out = import_workouts(engine, workouts)
    queue_auto_analysis(engine)  # analysed by the worker: the model call takes minutes
    return out


MAX_UPLOAD = 1024 * 1024 * 1024  # ponytail: whole body in memory; spool to disk if 1 GB hurts


@router.post("/imports/file")
async def upload_file(request: Request, db: Db) -> dict[str, Any]:
    """Raw file body (octet-stream) + X-Filename: FIT/GPX/TCX[.gz], Strava export zip, HAE json/zip."""
    name = unquote(request.headers.get("x-filename", ""))
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > MAX_UPLOAD:
            raise HTTPException(413, "file too large")
    db.commit()
    engine = cast(Engine, db.get_bind())
    try:
        out = await run_in_threadpool(import_upload, engine, name, bytes(buf))
    except Rejected as e:
        raise HTTPException(422, str(e)) from e
    queue_auto_analysis(engine)
    return out
