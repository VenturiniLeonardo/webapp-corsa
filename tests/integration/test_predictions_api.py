from datetime import UTC, datetime, timedelta

import pytest
import respx
from sqlalchemy.orm import Session
from test_stats_api import add, client, engine, env, metrics  # noqa: F401

from app.api import predictions
from app.domain.models import BestEffort, Setting
from app.metrics import prediction_weather as weather

NOW = datetime(2026, 10, 19, 12, tzinfo=UTC)


@pytest.fixture(autouse=True)
def prediction_clock(monkeypatch):
    monkeypatch.setattr(predictions, "_now", lambda: NOW)
    weather._cache.clear()


def populate(db_engine):
    with Session(db_engine) as s, s.begin():
        race = add(s, "2026-10-01T10:00:00+00:00", workout_type="race", mov=3500)
        for i in range(12):
            day = NOW - timedelta(days=i * 4)
            a = add(s, day.isoformat(), dist=8000, mov=3200)
            metrics(s, a, is_steady=True, gap_speed_ms=2.5, gps_suspect=False)
        return race.id


def test_standard_filters_bad_data_and_leaves_records_unchanged(engine, client):  # noqa: F811
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
        add(s, (NOW + timedelta(hours=1)).isoformat(), workout_type="race", mov=2000)
    result = client.get("/api/records/predictions")
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["model_version"] == "riegel-hr-weather-v2"
    assert data["from_date"] == "2026-08-25" and data["to_date"] == "2026-10-19"
    assert data["runs_8w"] == 13
    assert data["items"][1]["elapsed_s"] == pytest.approx(3500)
    assert data["items"][1]["anchor_activity_id"] == aid
    assert data["weather"]["status"] == "no_date"
    assert all(i["scenario"] is None for i in data["items"])
    assert client.get("/api/records").json() == before


def test_empty_and_audit_snapshot(client):  # noqa: F811
    data = client.get("/api/records/predictions?include_snapshot=true").json()
    assert len(data["items"]) == 4
    assert all(item["confidence"] == "unavailable" for item in data["items"])
    assert data["input_snapshot"]["cutoff_utc"] == NOW.isoformat()
    again = client.get("/api/records/predictions").json()
    assert again["input_snapshot"] is None
    assert again["input_hash"] == data["input_hash"]


def test_attestations_persist_and_update_only_predictions(engine, client):  # noqa: F811
    aid = populate(engine)
    client.headers["X-Corsa"] = "1"
    before = client.get("/api/records").json()
    url = f"/api/records/prediction-inputs/activities/{aid}"
    body = {
        "official_verified": True,
        "official_distance_m": 10000,
        "official_elapsed_s": 3400,
        "flat_confirmed": True,
    }
    assert client.put(url, json=body).status_code == 200
    profile = {
        "sensor": "chest",
        "hr_threshold": 170,
        "coverage": [{"from_date": "2026-08-25", "to_date": "2026-10-19"}],
    }
    assert client.put("/api/records/prediction-inputs/profile", json=profile).status_code == 200
    # Repeated saves must persist nested JSON, without overwriting profile.
    body["official_elapsed_s"] = 3300
    assert client.put(url, json=body).status_code == 200
    inputs = client.get("/api/records/prediction-inputs").json()
    assert inputs["activities"][str(aid)]["official_elapsed_s"] == 3300
    assert inputs["profile"]["sensor"] == "chest"
    data = client.get("/api/records/predictions").json()
    assert data["items"][1]["elapsed_s"] == pytest.approx(3300)
    assert data["items"][1]["sources"][0]["official"]
    assert data["coverage_fraction"] == 1
    assert client.get("/api/records").json() == before


def test_reject_unverified_inputs_and_nonexistent_tests(engine, client):  # noqa: F811
    client.headers["X-Corsa"] = "1"
    with Session(engine) as s, s.begin():
        a = add(s, NOW.isoformat())
        aid = a.id
        s.add(BestEffort(activity_id=aid, distance_m=5000, elapsed_s=1500, algo_version=1))
    url = f"/api/records/prediction-inputs/activities/{aid}"
    assert client.put(url, json={"official_verified": True}).status_code == 422
    assert (
        client.put(url, json={"official_verified": True, "official_distance_m": 10000}).status_code
        == 422
    )
    assert (
        client.put(
            url, json={"test_verified": True, "test_distance_m": 10000, "test_rpe": 9}
        ).status_code
        == 422
    )
    assert (
        client.put(
            url, json={"test_verified": True, "test_distance_m": 5000, "test_rpe": 9}
        ).status_code
        == 200
    )
    assert (
        client.put(
            "/api/records/prediction-inputs/profile", json={"hr_threshold": "nan"}
        ).status_code
        == 422
    )
    assert (
        client.put(
            "/api/records/prediction-inputs/profile",
            json={"coverage": [{"from_date": "2026-10-19", "to_date": "2026-08-25"}]},
        ).status_code
        == 422
    )


@respx.mock
def test_no_weather_request_without_date_or_when_disabled(engine, client):  # noqa: F811
    populate(engine)
    route = respx.get(weather.FORECAST_URL).respond(500)
    args = {"latitude": 45, "longitude": 7, "race_date": "2026-10-20"}
    assert (
        client.get("/api/records/predictions", params=args).json()["weather"]["status"]
        == "disabled"
    )
    with Session(engine) as s, s.begin():
        s.add(Setting(key="weather_enabled", value=True))
    assert client.get("/api/records/predictions").json()["weather"]["status"] == "no_date"
    assert (
        client.get("/api/records/predictions?race_date=2026-10-20").json()["weather"]["status"]
        == "no_location"
    )
    assert route.call_count == 0


@respx.mock
def test_forecast_changes_only_scenario_at_custom_location(engine, client):  # noqa: F811
    populate(engine)
    with Session(engine) as s, s.begin():
        s.add(Setting(key="weather_enabled", value=True))
    times = [datetime(2026, 10, 20, tzinfo=UTC).timestamp() + i * 3600 for i in range(48)]
    route = respx.get(weather.FORECAST_URL).respond(
        200,
        json={
            "hourly": {
                "time": times,
                "temperature_2m": [30] * len(times),
                "dew_point_2m": [22] * len(times),
            }
        },
    )
    before = client.get("/api/records/predictions").json()
    result = client.get(
        "/api/records/predictions",
        params={
            "latitude": 41.9028,
            "longitude": 12.4964,
            "location_name": "Roma, Lazio, Italia",
            "race_date": "2026-10-20",
            "race_time": "09:00",
            "race_timezone": "Europe/Rome",
        },
    )
    assert result.status_code == 200, result.text
    data = result.json()
    assert data["weather"]["status"] == "forecast"
    assert data["weather"]["location_name"] == "Roma, Lazio, Italia"
    assert data["weather"]["location_mode"] == "custom"
    assert data["weather"]["location_activity_id"] is None
    assert route.calls[0].request.url.params["latitude"] == "41.9"
    assert route.calls[0].request.url.params["longitude"] == "12.5"
    assert data["items"][1]["elapsed_s"] == before["items"][1]["elapsed_s"]
    assert data["items"][1]["scenario"]["elapsed_s"] > before["items"][1]["elapsed_s"]
    client.get(str(result.request.url))
    assert route.call_count == 1


@pytest.mark.parametrize(
    "params",
    [
        {"latitude": 45},
        {"longitude": 7},
        {"latitude": 91, "longitude": 7},
        {"latitude": 45, "longitude": -181},
        {"latitude": "nan", "longitude": 7},
        {"latitude": 45, "longitude": 7, "location_name": "x" * 161},
        {"race_date": "2026-10-18"},
        {"race_date": "2026-10-20", "race_month": 10},
        {"race_month": 13},
        {"race_timezone": "Not/AZone"},
        {"race_time": "25:00"},
    ],
)
def test_invalid_scenario_is_rejected(client, params):  # noqa: F811
    assert client.get("/api/records/predictions", params=params).status_code == 422


@respx.mock
def test_location_search_disambiguates_results_and_handles_outage(client):  # noqa: F811
    route = respx.get(predictions.GEOCODING_URL).respond(
        200,
        json={
            "results": [
                {
                    "id": 1,
                    "name": "Roma",
                    "admin1": "Lazio",
                    "country": "Italia",
                    "latitude": 41.9,
                    "longitude": 12.5,
                    "timezone": "Europe/Rome",
                },
                {
                    "id": 2,
                    "name": "Roma",
                    "country": "Australia",
                    "latitude": -26.6,
                    "longitude": 148.8,
                },
                {"id": 3, "name": "Broken", "latitude": 1000, "longitude": 0},
            ]
        },
    )
    response = client.get("/api/records/predictions/locations?q=Roma")
    assert [p["label"] for p in response.json()] == ["Roma, Lazio, Italia", "Roma, Australia"]
    assert response.json()[0]["timezone"] == "Europe/Rome"
    assert route.calls[0].request.url.params["language"] == "it"
    route.respond(200, json={})
    assert client.get("/api/records/predictions/locations?q=Unknown").json() == []
    route.respond(503)
    assert client.get("/api/records/predictions/locations?q=Roma").status_code == 502
