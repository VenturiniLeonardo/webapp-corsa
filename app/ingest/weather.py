"""Historical weather from Open-Meteo (opt-in, PLAN M8-05). No API key.

Privacy: sends the start coordinates rounded to 0.01° (~1 km) and the UTC date of the run.
Values are hourly reanalysis/forecast at the run midpoint, not measured on the route.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.domain.models import Activity, ActivityMetrics, Stream
from app.domain.stream_codec import decode_stream

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"  # serves recent past too
ARCHIVE_LAG_DAYS = 7  # ERA5 archive lags a few days behind
BATCH = 200  # ponytail: runs with no data upstream are retried each sync; add a marker if noisy


def _start_coords(s: Session, act: Activity) -> tuple[float, float] | None:
    sid = act.stream_source_id or act.primary_source_id
    st = s.get(Stream, sid) if sid else None
    if st is None:
        return None
    ch = decode_stream(st.data)
    for lat, lng in zip(ch.get("lat") or [], ch.get("lng") or [], strict=False):
        if lat is not None and lng is not None:
            return round(lat, 2), round(lng, 2)
    return None


def fetch(c: httpx.Client, lat: float, lng: float, when: datetime) -> tuple[float, float] | None:
    """(temperature °C, dew point °C) for the hour nearest `when` (UTC)."""
    when = (when + timedelta(minutes=30)).replace(minute=0, second=0, microsecond=0)
    day = when.date().isoformat()
    old = datetime.now(UTC) - when > timedelta(days=ARCHIVE_LAG_DAYS)
    r = c.get(
        ARCHIVE_URL if old else FORECAST_URL,
        params={
            "latitude": lat,
            "longitude": lng,
            "start_date": day,
            "end_date": day,
            "hourly": "temperature_2m,dew_point_2m",
            "timezone": "GMT",
        },
    )
    r.raise_for_status()
    h = r.json()["hourly"]
    temp, dew = h["temperature_2m"][when.hour], h["dew_point_2m"][when.hour]
    return None if temp is None or dew is None else (float(temp), float(dew))


def fill_missing(
    engine: Engine,
    progress: Callable[[int, int], None] = lambda done, total: None,
    http: httpx.Client | None = None,
) -> str | None:
    """Weather for outdoor GPS runs that have none yet, newest first. Returns an error note."""
    from app.worker.runner import adjusted_ef  # runner imports the job; avoid the cycle

    with Session(engine) as s:
        ids = list(
            s.scalars(
                select(Activity.id)
                .where(
                    Activity.weather_temp_c.is_(None),
                    Activity.has_gps.is_(True),
                    Activity.is_indoor.is_not(True),
                )
                .order_by(Activity.start_time_utc.desc())
                .limit(BATCH)
            )
        )
    failed = 0
    progress(0, len(ids))
    with http or httpx.Client(timeout=30) as c:
        for i, aid in enumerate(ids, 1):
            try:
                with Session(engine) as s, s.begin():
                    act = s.get(Activity, aid)
                    assert act is not None
                    ll = _start_coords(s, act)
                    mid = datetime.fromisoformat(act.start_time_utc) + timedelta(
                        seconds=(act.elapsed_s or 0) / 2
                    )
                    if ll and (w := fetch(c, *ll, mid.astimezone(UTC))):
                        act.weather_temp_c, act.weather_dew_point_c = w
                        if m := s.get(ActivityMetrics, aid):
                            m.ef_adjusted = adjusted_ef(act, m)
            except (httpx.HTTPError, KeyError, IndexError):  # bad/odd upstream reply
                failed += 1
            progress(i, len(ids))
    return f"{failed} activities failed" if failed else None
