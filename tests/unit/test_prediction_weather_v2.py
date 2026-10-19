from datetime import UTC, date, datetime, time, timedelta

import httpx
import pytest
import respx

from app.metrics import prediction_weather as weather

NOW = datetime(2026, 10, 19, 12, tzinfo=UTC)
ITEMS = [{"elapsed_s": t} for t in (1200, 2500, 5500, 12000)]


@pytest.fixture(autouse=True)
def clear_cache():
    weather._cache.clear()


@respx.mock
def test_weather_cache_validation_and_outage():
    route = respx.get(weather.FORECAST_URL).respond(
        200,
        json={"hourly": {"time": [NOW.timestamp()], "temperature_2m": [25], "dew_point_2m": [18]}},
    )
    params = weather._params(45, 7)
    first = weather._fetch(weather.FORECAST_URL, params, 900)
    assert weather._fetch(weather.FORECAST_URL, params, 900) == first and route.call_count == 1
    assert weather._nearest(first, NOW) == (25, 18)
    assert weather._nearest(first, NOW + timedelta(seconds=3601)) is None
    weather._cache.clear()
    route.respond(200, json={"hourly": {"time": [0], "temperature_2m": [], "dew_point_2m": []}})
    assert weather._fetch(weather.FORECAST_URL, params, 900) is None
    weather._cache.clear()
    route.mock(side_effect=httpx.ConnectError("offline"))
    assert weather._fetch(weather.FORECAST_URL, params, 900) is None


def test_nearest_ties_earlier_and_rejects_invalid():
    h = {
        "time": [NOW.timestamp() - 1800, NOW.timestamp() + 1800, float("nan")],
        "temperature_2m": [20, 30, 10],
        "dew_point_2m": [10, 20, 5],
    }
    assert weather._nearest({"hourly": h}, NOW) == (20, 10)


def test_seasonal_years_have_equal_mass_and_need_five(monkeypatch):
    def samples(year, *_):
        # Some years have fewer available days: their weight stays equal.
        return ([(year - 2000, 0)] * (1 if year % 2 else 3), NOW.isoformat())

    monkeypatch.setattr(weather, "_year_samples", samples)
    meta, rows = weather.scenarios(ITEMS, NOW, 45, 7, None, 11, time(9), "Europe/Rome", True)
    assert meta["status"] == "climatology" and meta["sample_years"] == 10
    by_temp = {}
    for t, _, w in rows[0]["samples"]:
        by_temp[t] = by_temp.get(t, 0) + w
    assert all(w == pytest.approx(1) for w in by_temp.values())
    monkeypatch.setattr(
        weather,
        "_year_samples",
        lambda year, *_: ([(20, 10)], "now") if year >= 2022 else ([], None),
    )
    meta, rows = weather.scenarios(ITEMS, NOW, 45, 7, None, 11, time(9), "Europe/Rome", True)
    assert meta["status"] == "unavailable" and not rows[0]["samples"]


@pytest.mark.parametrize(
    "day,clock", [(date(2026, 10, 25), time(2, 30)), (date(2027, 3, 28), time(2, 30))]
)
def test_dst_ambiguous_and_nonexistent_times_are_explicit(day, clock):
    meta, _ = weather.scenarios(ITEMS, NOW, 45, 7, day, None, clock, "Europe/Rome", True)
    assert meta["status"] == "unavailable" and "ambiguo" in meta["message"]


def test_archive_samples_only_pre_cutoff_and_nearest_hour(monkeypatch):
    stamps = [datetime(2025, 11, 15, h, tzinfo=UTC).timestamp() for h in (8, 9, 10)]
    monkeypatch.setattr(
        weather,
        "_fetch",
        lambda *_: {
            "hourly": {
                "time": stamps + [NOW.timestamp(), float("inf")],
                "temperature_2m": [12, 13, 14, 30, 40],
                "dew_point_2m": [5] * 5,
            },
            "retrieved_at": "saved",
        },
    )
    rows, _ = weather._year_samples(2025, 11, 15, datetime(2027, 11, 15, 9, tzinfo=UTC), 45, 7, NOW)
    assert rows == [(13, 5)]
