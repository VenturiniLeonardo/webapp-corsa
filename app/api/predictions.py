"""Read-only estimates for the Records page; no persisted predictions or migrations."""

import math
import threading
import time
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.activities import Db
from app.domain.models import Activity, ActivityMetrics, BestEffort, Setting
from app.ingest.weather import FORECAST_URL, _start_coords
from app.metrics.predictions import Anchor, Run, predict

router = APIRouter(prefix="/api")
_cache: dict[tuple[float, float], tuple[float, dict[str, Any] | None]] = {}
_lock = threading.Lock()
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"


class Location(BaseModel):
    id: int
    label: str
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)


class Weather(BaseModel):
    status: Literal["current", "unavailable", "disabled", "no_location"]
    temp_c: float | None = None
    dew_point_c: float | None = None
    observed_at: str | None = None
    location_activity_id: int | None = None
    location_mode: Literal["auto", "custom"] = "auto"
    location_name: str | None = None
    source: str = "Open-Meteo"


class Prediction(BaseModel):
    distance_m: float
    label: str
    elapsed_s: int | None
    pace_s_per_km: float | None
    pace_low_s_per_km: float | None
    pace_high_s_per_km: float | None
    confidence: Literal["medium", "low", "unavailable"]
    reasons: list[str]
    anchor_activity_id: int | None


class Predictions(BaseModel):
    model_version: str = "riegel-hr-weather-v1"
    generated_at: str
    from_date: date
    to_date: date
    weather: Weather
    items: list[Prediction]
    runs_8w: int
    hr_runs_8w: int
    steady_hr_runs_8w: int
    hr_comparable_pairs: int
    hr_trend_applied: bool
    hr_efficiency_change_pct: float | None
    avg_weekly_distance_m: float
    longest_run_m: float
    heat_adjustment_pct: float
    notices: list[str]


def _now() -> datetime:
    return datetime.now(UTC)


def fetch_current(lat: float, lng: float) -> dict[str, Any] | None:
    """Fresh hourly weather, cached 15 min; failed requests retried after 1 min.

    Same rounded location and opt-in as historical weather. Never substitute
    old activity weather for current conditions or serve an expired observation.
    """
    key = (lat, lng)
    with _lock:
        cached = _cache.get(key)
        if cached and time.monotonic() < cached[0]:
            return cached[1]
    result = None
    try:
        with httpx.Client(timeout=4) as client:
            response = client.get(
                FORECAST_URL,
                params={
                    "latitude": lat,
                    "longitude": lng,
                    "hourly": "temperature_2m,dew_point_2m",
                    "forecast_hours": 2,
                    "timezone": "GMT",
                    "timeformat": "unixtime",
                },
            )
            response.raise_for_status()
            hourly = response.json()["hourly"]
            now = _now().timestamp()
            idx = min(range(len(hourly["time"])), key=lambda i: abs(hourly["time"][i] - now))
            stamp = float(hourly["time"][idx])
            temp = float(hourly["temperature_2m"][idx])
            dew = float(hourly["dew_point_2m"][idx])
            if (
                abs(stamp - now) <= 5400
                and math.isfinite(temp)
                and math.isfinite(dew)
                and -60 <= dew <= temp <= 60
            ):
                result = {
                    "temp_c": temp,
                    "dew_point_c": dew,
                    "observed_at": datetime.fromtimestamp(stamp, UTC).isoformat(),
                }
    except (httpx.HTTPError, ValueError, TypeError, KeyError, IndexError, OverflowError):
        pass
    with _lock:
        if len(_cache) >= 32:
            _cache.clear()
        _cache[key] = (time.monotonic() + (900 if result else 60), result)
    return result


def _weather(
    db: Db,
    activities: list[Activity],
    today: date,
    latitude: float | None = None,
    longitude: float | None = None,
    location_name: str | None = None,
) -> Weather:
    custom = latitude is not None and longitude is not None
    mode: Literal["auto", "custom"] = "custom" if custom else "auto"
    enabled = db.get(Setting, "weather_enabled")
    if not enabled or not enabled.value:
        return Weather(status="disabled", location_mode=mode, location_name=location_name)
    if latitude is not None and longitude is not None:
        data = fetch_current(round(latitude, 2), round(longitude, 2))
        return Weather(
            status="current" if data else "unavailable",
            location_mode="custom",
            location_name=location_name or f"{latitude:.2f}, {longitude:.2f}",
            **(data or {}),
        )
    for act in reversed(activities):
        if act.local_date < (today - timedelta(days=55)).isoformat() or not act.has_gps:
            continue
        try:
            coords = _start_coords(db, act)
        except (ValueError, OSError, KeyError, TypeError):
            continue
        if coords:
            data = fetch_current(*coords)
            return Weather(
                status="current" if data else "unavailable",
                location_activity_id=act.id,
                **(data or {}),
            )
    return Weather(status="no_location")


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
) -> Predictions:
    if (latitude is None) != (longitude is None):
        raise HTTPException(422, "Latitudine e longitudine devono essere specificate insieme.")
    now = _now()
    # local_date is in the runner's timezone, not the UTC calendar day.
    today = now.astimezone(ZoneInfo("Europe/Rome")).date()
    rows = db.execute(
        select(Activity, ActivityMetrics)
        .outerjoin(ActivityMetrics, Activity.id == ActivityMetrics.activity_id)
        .where(
            Activity.excluded_from_stats.is_(False),
            Activity.duplicate_of_id.is_(None),
            Activity.upstream_deleted_at.is_(None),
            Activity.is_indoor.is_not(True),
            Activity.sport_type == "run",
            Activity.local_date <= today.isoformat(),
            Activity.distance_m >= 1000,
            Activity.moving_s > 0,
            Activity.elapsed_s > 0,
            ActivityMetrics.gps_suspect.is_not(True),
        )
        .order_by(Activity.local_date, Activity.id)
    ).all()
    runs, activities = [], []
    for a, m in rows:
        distance, moving, elapsed = (
            float(a.distance_m or 0),
            float(a.moving_s or 0),
            float(a.elapsed_s or 0),
        )
        if not all(math.isfinite(x) for x in (distance, moving, elapsed)):
            continue
        if not 1 <= distance / moving <= 8 or elapsed < moving * 0.95:
            continue
        runs.append(
            Run(
                a.id,
                date.fromisoformat(a.local_date),
                distance,
                elapsed,
                moving,
                a.avg_hr,
                bool(m and m.is_steady),
                m.gap_speed_ms if m else None,
                a.weather_temp_c,
                a.weather_dew_point_c,
                a.workout_type == "race",
            )
        )
        activities.append(a)
    by_id = {r.id: r for r in runs}
    anchors = [
        Anchor(r, r.distance_m, r.elapsed_s, True) for r in runs if r.race and r.distance_m >= 3000
    ]
    # At most one best-effort anchor per activity, to avoid counting overlapping
    # 5K/10K/half segments of one run as independent performances.
    efforts: dict[int, BestEffort] = {}
    for e in db.scalars(
        select(BestEffort).where(BestEffort.distance_m >= 3000, BestEffort.elapsed_s >= 600)
    ):
        if e.activity_id not in by_id or by_id[e.activity_id].race:
            continue
        if (
            e.elapsed_s > by_id[e.activity_id].elapsed_s
            or e.distance_m > by_id[e.activity_id].distance_m * 1.02
        ):
            continue
        previous = efforts.get(e.activity_id)
        if previous is None or e.distance_m > previous.distance_m:
            efforts[e.activity_id] = e
    anchors.extend(
        Anchor(by_id[e.activity_id], e.distance_m, e.elapsed_s, False) for e in efforts.values()
    )
    weather = _weather(db, activities, today, latitude, longitude, location_name)
    result = predict(runs, anchors, today, weather.temp_c, weather.dew_point_c)
    return Predictions(
        generated_at=now.isoformat(),
        from_date=today - timedelta(days=55),
        to_date=today,
        weather=weather,
        **result,
    )
