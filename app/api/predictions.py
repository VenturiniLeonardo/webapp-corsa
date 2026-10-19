"""Records predictions v2: standard estimate plus a separately dated weather scenario."""

import hashlib
import json
from dataclasses import asdict
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.api.activities import Db
from app.api.prediction_inputs import Profile, read_inputs
from app.domain.models import Activity, Setting
from app.ingest.weather import _start_coords
from app.metrics.prediction_data import load_data
from app.metrics.prediction_weather import scenarios
from app.metrics.predictions_v2 import apply_weather, predict_v2

router = APIRouter(prefix="/api")
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"


class Location(BaseModel):
    id: int
    label: str
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    timezone: str = "UTC"


class Prediction(BaseModel):
    model_config = ConfigDict(extra="allow")
    distance_m: float
    label: str
    elapsed_s: float | None
    pace_s_per_km: float | None
    pace_low_s_per_km: float | None
    pace_high_s_per_km: float | None
    confidence: Literal["medium", "low", "unavailable"]
    reasons: list[str]
    anchor_activity_id: int | None
    sources: list[dict[str, Any]]
    scenario: dict[str, Any] | None = None


class Predictions(BaseModel):
    model_config = ConfigDict(extra="allow")
    model_version: str
    generated_at: str
    from_date: date
    to_date: date
    weather: dict[str, Any]
    items: list[Prediction]
    input_hash: str
    input_snapshot: dict[str, Any] | None = None


def _now() -> datetime:
    return datetime.now(UTC)


def _coords(db: Db, activities: list[Activity], today: date) -> tuple[float, float, int] | None:
    for act in reversed(activities):
        if not act.has_gps or act.local_date < (today - timedelta(days=55)).isoformat():
            continue
        try:
            coords = _start_coords(db, act)
        except (ValueError, OSError, EOFError, TypeError, KeyError):
            continue
        if coords:
            return coords[0], coords[1], act.id
    return None


@router.get("/records/predictions/locations", response_model=list[Location])
def search_locations(q: Annotated[str, Query(min_length=2, max_length=100)]) -> list[Location]:
    """Explicit city search: names go to Open-Meteo, never activity coordinates."""
    if len(q.strip()) < 2:
        raise HTTPException(422, "Inserisci almeno due caratteri.")
    try:
        with httpx.Client(timeout=4) as client:
            response = client.get(
                GEOCODING_URL,
                params={"name": q.strip(), "count": 8, "language": "it", "format": "json"},
            )
            response.raise_for_status()
            rows = response.json().get("results", [])
        results = []
        for row in rows:
            try:
                parts = list(
                    dict.fromkeys(str(row[k]) for k in ("name", "admin1", "country") if row.get(k))
                )
                if not row.get("name"):
                    continue
                results.append(
                    Location(
                        id=row["id"],
                        label=", ".join(parts)[:160],
                        latitude=row["latitude"],
                        longitude=row["longitude"],
                        timezone=row.get("timezone", "UTC"),
                    )
                )
            except (ValueError, TypeError, KeyError):
                continue
        return results
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        raise HTTPException(502, "Ricerca località temporaneamente non disponibile.") from None


@router.get("/records/predictions", response_model=Predictions)
def race_predictions(
    db: Db,
    latitude: Annotated[float | None, Query(ge=-90, le=90, allow_inf_nan=False)] = None,
    longitude: Annotated[float | None, Query(ge=-180, le=180, allow_inf_nan=False)] = None,
    location_name: Annotated[str | None, Query(max_length=160)] = None,
    race_date: date | None = None,
    race_month: Annotated[int | None, Query(ge=1, le=12)] = None,
    race_time: time | None = None,
    race_timezone: Annotated[str, Query(max_length=80)] = "Europe/Rome",
    include_snapshot: bool = False,
) -> Predictions:
    if (latitude is None) != (longitude is None):
        raise HTTPException(422, "Latitudine e longitudine devono essere specificate insieme.")
    if race_date and race_month:
        raise HTTPException(422, "Scegli una data oppure un mese, non entrambi.")
    try:
        ZoneInfo(race_timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(422, "Fuso orario IANA non valido.") from None
    now = _now()
    today = now.astimezone(ZoneInfo("Europe/Rome")).date()
    if race_date and race_date < today:
        raise HTTPException(422, "La data gara non può essere nel passato.")
    inputs = read_inputs(db)
    profile = Profile.model_validate(inputs.get("profile", {}))
    runs, refs, activities, notices = load_data(db, today, inputs, now)
    intervals = [(row.from_date, row.to_date) for row in profile.coverage]
    result = predict_v2(runs, refs, today, intervals, profile.taper_start)
    result["notices"].extend(notices)
    custom = latitude is not None and longitude is not None
    location_id = None
    if not custom and (race_date or race_month):
        coords = _coords(db, activities, today)
        if coords:
            latitude, longitude, location_id = coords
    enabled = db.get(Setting, "weather_enabled")
    weather, weather_rows = scenarios(
        result["items"],
        now,
        latitude,
        longitude,
        race_date,
        race_month,
        race_time,
        race_timezone,
        bool(enabled and enabled.value),
    )
    weather.update(
        location_name=location_name if custom else "Zona dell'ultima corsa GPS",
        location_mode="custom" if custom else "auto",
        location_activity_id=location_id,
    )
    apply_weather(result["items"], weather_rows)
    snapshot = {
        "cutoff_utc": now.isoformat(),
        "available_at": _now().isoformat(),
        "today": today.isoformat(),
        "runs": [asdict(r) for r in runs],
        "references": [asdict(r) for r in refs],
        "inputs": inputs,
        "parameters": result["parameters"],
        "model_version": result["model_version"],
        "weather": weather,
        "weather_samples": weather_rows,
    }
    digest = hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, default=str, allow_nan=False).encode()
    ).hexdigest()
    return Predictions(
        generated_at=_now().isoformat(),
        from_date=today - timedelta(days=55),
        to_date=today,
        weather=weather,
        input_hash=digest,
        input_snapshot=snapshot if include_snapshot else None,
        **result,
    )
