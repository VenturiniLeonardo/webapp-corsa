from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from sqlalchemy.orm import Session
from test_stats_api import add, client, engine, env, metrics  # noqa: F401

from app.api import predictions
from app.domain.models import Activity, Setting

NOW = datetime(2026, 10, 19, 12, tzinfo=UTC)


@pytest.fixture(autouse=True)
def prediction_clock(monkeypatch):
    monkeypatch.setattr(predictions, "_now", lambda: NOW)
    predictions._cache.clear()


def populate(db_engine):
    with Session(db_engine) as s, s.begin():
        race = add(s, "2026-10-01T10:00:00+00:00", workout_type="race", mov=3500)
        for i in range(12):
            day = NOW - timedelta(days=i * 4)
            a = add(s, day.isoformat(), dist=8000, mov=3200)
            metrics(s, a, is_steady=True, gap_speed_ms=2.5, gps_suspect=False)
        return race.id


def test_prediction_endpoint_filters_bad_data_and_leaves_records_unchanged(engine, client):  # noqa: F811
    aid = populate(engine)
    before = client.get("/api/records").json()
    with Session(engine) as s, s.begin():
        for kw in (
            {"excluded_from_stats": True},
            {"duplicate_of_id": aid},
            {"is_indoor": True},
            {"sport_type": "trail_run"},
            {"upstream_deleted_at": NOW.isoformat()},
        ):
            add(s, NOW.isoformat(), workout_type="race", mov=2000, **kw)
        bad = add(s, NOW.isoformat(), workout_type="race", mov=2000)
        metrics(s, bad, gps_suspect=True)
    result = client.get("/api/records/predictions")
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["from_date"] == "2026-08-25" and data["to_date"] == "2026-10-19"
    assert data["runs_8w"] == 13
    assert data["items"][1]["elapsed_s"] == 3500
    assert data["items"][1]["anchor_activity_id"] == aid
    assert data["weather"]["status"] == "disabled"
    assert client.get("/api/records").json() == before


def test_empty_prediction_endpoint_is_explicit(client):  # noqa: F811
    data = client.get("/api/records/predictions").json()
    assert len(data["items"]) == 4
    assert all(item["confidence"] == "unavailable" for item in data["items"])


@respx.mock
def test_weather_is_fresh_cached_and_failure_safe():
    route = respx.get(predictions.FORECAST_URL).respond(
        200,
        json={
            "hourly": {
                "time": [NOW.timestamp()],
                "temperature_2m": [25],
                "dew_point_2m": [18],
            }
        },
    )
    first = predictions.fetch_current(45, 7)
    assert first and first["temp_c"] == 25
    assert predictions.fetch_current(45, 7) == first and route.call_count == 1
    predictions._cache.clear()
    route.respond(200, json={"hourly": {"time": [0], "temperature_2m": [25], "dew_point_2m": [18]}})
    assert predictions.fetch_current(45, 7) is None  # stale weather never shown as current
    predictions._cache.clear()
    route.mock(side_effect=httpx.ConnectError("offline"))
    assert predictions.fetch_current(45, 7) is None


@respx.mock
def test_no_weather_request_when_disabled_or_location_missing(engine, client):  # noqa: F811
    populate(engine)
    route = respx.get(predictions.FORECAST_URL).respond(500)
    assert client.get("/api/records/predictions").json()["weather"]["status"] == "disabled"
    with Session(engine) as s, s.begin():
        s.add(Setting(key="weather_enabled", value=True))
    assert client.get("/api/records/predictions").json()["weather"]["status"] == "no_location"
    assert route.call_count == 0


@respx.mock
def test_current_weather_changes_prediction_and_reports_location(engine, client, monkeypatch):  # noqa: F811
    aid = populate(engine)
    cool = client.get("/api/records/predictions").json()
    with Session(engine) as s, s.begin():
        s.add(Setting(key="weather_enabled", value=True))
        act = s.get(Activity, aid)
        act.has_gps = True
    monkeypatch.setattr(predictions, "_start_coords", lambda *_: (45.0, 7.0))
    respx.get(predictions.FORECAST_URL).respond(
        200,
        json={
            "hourly": {
                "time": [NOW.timestamp()],
                "temperature_2m": [30],
                "dew_point_2m": [22],
            }
        },
    )
    hot = client.get("/api/records/predictions").json()
    assert hot["weather"]["status"] == "current"
    assert hot["weather"]["location_activity_id"] == aid
    assert hot["heat_adjustment_pct"] > 0
    assert hot["items"][1]["elapsed_s"] > cool["items"][1]["elapsed_s"]
