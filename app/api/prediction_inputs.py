"""Explicit user attestations for v2; stored independently of synced activity data."""

from copy import deepcopy
from datetime import UTC, date, datetime
from typing import Any, Literal, Self

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from app.api.activities import Db
from app.domain.models import Activity, BestEffort, Setting

router = APIRouter(prefix="/api/records/prediction-inputs")
KEY = "prediction_inputs_v2"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Coverage(Strict):
    from_date: date
    to_date: date

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.to_date < self.from_date:
            raise ValueError("Intervallo storico invertito")
        return self


class Profile(Strict):
    coverage: list[Coverage] = Field(default_factory=list, max_length=100)
    sensor: Literal["unknown", "optical", "chest"] = "unknown"
    sensor_id: str | None = Field(None, max_length=100)
    hr_threshold: float | None = Field(None, ge=80, le=230, allow_inf_nan=False)
    hr_max_measured: float | None = Field(None, ge=100, le=250, allow_inf_nan=False)
    taper_start: date | None = None


class ActivityInput(Strict):
    official_verified: bool = False
    official_distance_m: float | None = Field(None, ge=3000, le=100000, allow_inf_nan=False)
    official_elapsed_s: float | None = Field(None, ge=300, le=86400, allow_inf_nan=False)
    flat_confirmed: bool = False
    test_verified: bool = False
    test_distance_m: float | None = Field(None, ge=3000, le=50000, allow_inf_nan=False)
    test_rpe: float | None = Field(None, ge=0, le=10, allow_inf_nan=False)
    sensor: Literal["unknown", "optical", "chest"] | None = None
    sensor_id: str | None = Field(None, max_length=100)
    baseline_volume_km: float | None = Field(None, ge=0, le=300, allow_inf_nan=False)
    baseline_long_km: float | None = Field(None, ge=0, le=150, allow_inf_nan=False)

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if self.official_verified and self.official_distance_m is None:
            raise ValueError("Indica la distanza ufficiale verificata")
        if self.test_verified and (self.test_distance_m is None or self.test_rpe is None):
            raise ValueError("Indica distanza e RPE del test continuo")
        return self


def read_inputs(db: Db) -> dict[str, Any]:
    setting = db.get(Setting, KEY)
    return (
        deepcopy(setting.value)
        if setting and isinstance(setting.value, dict)
        else {"profile": {}, "activities": {}}
    )


def save_inputs(db: Db, value: dict[str, Any]) -> None:
    value["updated_at"] = datetime.now(UTC).isoformat()
    db.merge(Setting(key=KEY, value=value))
    db.commit()


@router.get("")
def get_inputs(db: Db) -> dict[str, Any]:
    value = read_inputs(db)
    activities = []
    rows = db.scalars(
        select(Activity)
        .where(
            Activity.excluded_from_stats.is_(False),
            Activity.duplicate_of_id.is_(None),
            Activity.upstream_deleted_at.is_(None),
            Activity.sport_type == "run",
            Activity.is_indoor.is_not(True),
        )
        .order_by(Activity.local_date.desc(), Activity.id.desc())
    )
    efforts: dict[int, list[float]] = {}
    for aid, distance in db.execute(
        select(BestEffort.activity_id, BestEffort.distance_m).where(BestEffort.distance_m >= 3000)
    ):
        efforts.setdefault(aid, []).append(distance)
    for a in rows:
        activities.append(
            {
                "id": a.id,
                "date": a.local_date,
                "name": a.name,
                "race": a.workout_type == "race",
                "distance_m": a.distance_m,
                "elapsed_s": a.elapsed_s,
                "test_distances": sorted(efforts.get(a.id, [])),
            }
        )
    return {
        **value,
        "profile": Profile.model_validate(value.get("profile", {})),
        "available_activities": activities,
    }


@router.put("/profile")
def put_profile(body: Profile, db: Db) -> dict[str, Any]:
    value = read_inputs(db)
    value["profile"] = body.model_dump(mode="json")
    save_inputs(db, value)
    return value


@router.put("/activities/{activity_id}")
def put_activity(activity_id: int, body: ActivityInput, db: Db) -> dict[str, Any]:
    act = db.get(Activity, activity_id)
    if not act or act.upstream_deleted_at or act.excluded_from_stats or act.duplicate_of_id:
        raise HTTPException(404, "Corsa non disponibile")
    if body.official_verified and act.workout_type != "race":
        raise HTTPException(422, "La conferma ufficiale richiede una corsa classificata come gara")
    if body.test_verified:
        found = db.scalar(
            select(BestEffort.id).where(
                BestEffort.activity_id == activity_id, BestEffort.distance_m == body.test_distance_m
            )
        )
        if found is None:
            raise HTTPException(422, "Nessun best effort disponibile sulla distanza del test")
    value = read_inputs(db)
    value.setdefault("activities", {})[str(activity_id)] = body.model_dump(mode="json")
    save_inputs(db, value)
    return value
