from itertools import pairwise
from typing import Any, cast

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import Engine, select

from app.api.activities import Db
from app.domain.models import Setting
from app.worker.queue import JobQueue

router = APIRouter(prefix="/api")
KEYS = ("hr_max", "hr_rest", "hr_zones", "steady_cv_threshold")
RECOMPUTE_KEYS = {"hr_zones", "steady_cv_threshold"}  # the two inputs of the metrics cfg


class SettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hr_max: int | None = Field(None, ge=100, le=250)
    hr_rest: int | None = Field(None, ge=20, le=120)
    hr_zones: list[int] | None = Field(None, min_length=4, max_length=4)  # Z1-Z4 upper bounds
    steady_cv_threshold: float | None = Field(None, gt=0, le=1)

    @field_validator("hr_zones")
    @classmethod
    def ascending(cls, v: list[int] | None) -> list[int] | None:
        if v is not None and (v[0] <= 0 or any(a >= b for a, b in pairwise(v))):
            raise ValueError("hr_zones must be strictly ascending")
        return v


def _read(db: Db) -> dict[str, Any]:
    d: dict[str, Any] = dict(
        db.execute(select(Setting.key, Setting.value).where(Setting.key.in_(KEYS))).all()
    )
    return {k: d.get(k) for k in KEYS}


@router.get("/settings")
def read_settings(db: Db) -> dict[str, Any]:
    return _read(db)


@router.put("/settings")
def put_settings(body: SettingsIn, db: Db) -> dict[str, Any]:
    old, changed = _read(db), set()
    for k, v in body.model_dump(exclude_unset=True, exclude_none=True).items():
        if old[k] != v:
            changed.add(k)
            db.merge(Setting(key=k, value=v))
    db.commit()
    job_id = None
    if changed & RECOMPUTE_KEYS:
        job_id = JobQueue(cast(Engine, db.get_bind())).enqueue("recompute", {})
    return {"settings": _read(db), "recompute_job_id": job_id}
