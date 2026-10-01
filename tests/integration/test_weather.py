import httpx
import respx
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_file_import import _gpx, _post
from test_hae_import import client  # noqa: F401

from app.domain.models import Activity, ActivityMetrics, Lap
from app.ingest import weather


@respx.mock
def test_fill_missing_sets_weather_and_adjusted_ef(client):  # noqa: F811
    c, e = client
    assert _post(c, "a.gpx", _gpx()).status_code == 200
    hourly = {"temperature_2m": [20.0] * 24, "dew_point_2m": [18.0] * 24}
    hourly["temperature_2m"][7] = 30.0  # run is at 07:00 UTC
    hourly["dew_point_2m"][7] = 22.0
    route = respx.get(url__regex=r"https://(archive-)?api\.open-meteo\.com/.*").respond(
        200, json={"hourly": hourly}
    )
    assert weather.fill_missing(e, http=httpx.Client()) is None
    q = route.calls[0].request.url.params
    assert (q["latitude"], q["longitude"], q["start_date"]) == ("45.0", "12.0", "2026-09-20")
    with Session(e) as s:
        a = s.scalars(select(Activity)).one()
        assert (a.weather_temp_c, a.weather_dew_point_c) == (30.0, 22.0)
        m = s.get(ActivityMetrics, a.id)
        assert m is not None and m.gap_speed_ms and m.gap_speed_ms > a.distance_m / a.moving_s
        assert m.ef_adjusted is None or m.ef_adjusted > 0
        assert s.scalars(select(Lap.gap_speed_ms).where(Lap.kind == "split_km")).all()
    weather.fill_missing(e, http=httpx.Client())
    assert route.call_count == 1  # already filled: not fetched again
