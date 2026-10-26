from datetime import UTC, datetime
from datetime import date as Date
from itertools import pairwise
from typing import Any, cast

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import Engine, select

from app.api.activities import Db
from app.domain.models import Setting
from app.metrics.vo2max_data import MODEL_VERSION, estimate_on, model_config
from app.worker.queue import JobQueue

router = APIRouter(prefix="/api")
KEYS = (
    "hr_max",
    "hr_rest",
    "hr_zones",
    "steady_cv_threshold",
    "ref_pace_s_per_km",
    "weather_enabled",
    "cycle_start",
    "races",
    "vo2max_reference",
)
CLEARABLE = {"cycle_start", "vo2max_reference"}  # null clears it; other keys ignore null
RECOMPUTE_KEYS = {"hr_zones", "steady_cv_threshold", "ref_pace_s_per_km"}  # metrics cfg inputs


class Vo2maxReferenceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: float = Field(ge=14, le=65, allow_inf_nan=False)
    date: Date

    @field_validator("date")
    @classmethod
    def not_future(cls, v: Date) -> Date:
        if v > datetime.now(UTC).date():
            raise ValueError("La data Apple Watch non può essere futura")
        return v


class SettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hr_max: int | None = Field(None, ge=100, le=250)
    hr_rest: int | None = Field(None, ge=20, le=120)
    hr_zones: list[int] | None = Field(None, min_length=4, max_length=4)  # Z1-Z4 upper bounds
    steady_cv_threshold: float | None = Field(None, gt=0, le=1)
    ref_pace_s_per_km: int | None = Field(None, ge=150, le=600)  # HR-at-reference-pace target
    weather_enabled: bool | None = None  # opt-in: sends rounded coords + date to Open-Meteo
    cycle_start: Date | None = None  # first day of a load week 1 in the 3+1 cycle
    races: list[Date] | None = Field(None, max_length=100)
    vo2max_reference: Vo2maxReferenceIn | None = None

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
    updates = body.model_dump(exclude_unset=True, mode="json")
    reference = body.vo2max_reference
    if reference is not None:
        effective = {**old, **{k: v for k, v in updates.items() if v is not None}}
        # Repeated ordinary saves preserve the original baseline snapshot. Explicit
        # reference/config changes take a new snapshot at the reference date.
        previous = old.get("vo2max_reference")
        if (
            previous
            and all(previous.get(k) == updates["vo2max_reference"][k] for k in ("value", "date"))
            and previous.get("config") == model_config(effective)
            and previous.get("model_version") == MODEL_VERSION
        ):
            updates["vo2max_reference"] = previous
        else:
            baseline = estimate_on(db, reference.date, effective)
            if baseline.value is None:
                raise HTTPException(
                    422,
                    "Per calibrare, imposta FC massima e a riposo valide e scegli una data con corse utilizzabili nei 28 giorni precedenti (data inclusa).",
                )
            updates["vo2max_reference"] = {
                **updates["vo2max_reference"],
                "baseline": baseline.value,
                "baseline_n": baseline.n,
                "config": model_config(effective),
                "model_version": MODEL_VERSION,
            }
    for k, v in updates.items():
        if (v is None and k not in CLEARABLE) or old[k] == v:
            continue
        changed.add(k)
        db.merge(Setting(key=k, value=v))
    db.commit()
    job_id, q = None, JobQueue(cast(Engine, db.get_bind()))
    if changed & RECOMPUTE_KEYS:
        job_id = q.enqueue("recompute", {})
    if "weather_enabled" in changed and body.weather_enabled:
        q.enqueue("weather", {})
    return {"settings": _read(db), "recompute_job_id": job_id}
