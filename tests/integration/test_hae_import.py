import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_session, make_engine
from app.domain.models import Activity, Base, Stream
from app.domain.stream_codec import decode_stream
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    for k, v in {
        "ALLOWED_LOGINS": "me",
        "DATABASE_URL": "sqlite://",
        "ENV": "dev",
        "AUTH_DEV_LOGIN": "me",
    }.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    e = make_engine(f"sqlite:///{tmp_path / 'h.db'}")
    Base.metadata.create_all(e)

    def sess():
        with Session(e) as s:
            yield s

    app.dependency_overrides[get_session] = sess
    yield TestClient(app), e
    app.dependency_overrides.clear()
    get_settings.cache_clear()


def _workout(wid="W1", name="Outdoor Run"):
    route = [
        {
            "timestamp": f"2026-09-29 18:05:{i:02d} +0200",
            "latitude": 45.0 + i * 1e-4,
            "longitude": 12.0,
            "altitude": 10.0 + i * 0.5,
            "speed": 3.0,
        }
        for i in range(10, 50)
    ]
    return {
        "id": wid,
        "name": name,
        "start": "2026-09-29 18:05:10 +0200",
        "end": "2026-09-29 18:05:50 +0200",
        "duration": 40.0,
        "distance": {"qty": 0.12, "units": "km"},
        "activeEnergyBurned": {"qty": 418.4, "units": "kJ"},
        "stepCadence": {"qty": 160.0, "units": "count/min"},
        "avgHeartRate": {"qty": 140, "units": "bpm"},
        "heartRateData": [{"Avg": 140, "date": "2026-09-29 18:05:00 +0200"}],
        "stepCount": [{"qty": 80, "date": "2026-09-29 18:05:00 +0200"}],
        "route": route,
    }


def test_import_idempotent_and_mapped(client):
    c, e = client
    h = {"X-Corsa": "1"}
    body = {"data": {"workouts": [_workout(), _workout("B", "Outdoor Cycling")]}}
    r = c.post("/api/imports/health-auto-export", json=body, headers=h)
    assert r.json() == {"mapped": 1, "skipped": 1, "duplicate": 0, "failed": []}
    assert c.post("/api/imports/health-auto-export", json=body, headers=h).json()["mapped"] == 1
    with Session(e) as s:
        assert s.scalar(select(func.count()).select_from(Activity)) == 1
        a = s.scalars(select(Activity)).one()
        assert (a.local_date, a.timezone, a.calories_kcal, a.distance_m) == (
            "2026-09-29",
            "Europe/Rome",
            100,
            120.0,
        )
        assert a.start_time_utc == "2026-09-29T16:05:10+00:00" and a.elev_gain_m == 19.0
        ch = decode_stream(s.scalars(select(Stream)).one().data)
        # 40 s window, 40 s of steps in a minute bucket -> 80 * 60/40 = 120 spm; 45 s tail excluded
        assert ch["cadence"][0] == 120.0 and ch["hr"][0] == 140
        assert ch["distance"][-1] > 400  # haversine: 39 * 1e-4 deg lat


def test_bad_body(client):
    c, _ = client
    assert (
        c.post("/api/imports/health-auto-export", json={}, headers={"X-Corsa": "1"}).status_code
        == 422
    )
    assert c.post("/api/imports/health-auto-export", json={}).status_code == 403
