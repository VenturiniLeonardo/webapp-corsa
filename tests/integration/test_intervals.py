import gzip

import httpx
import respx
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_file_import import _gpx
from test_hae_import import client  # noqa: F401

from app.core.config import get_settings
from app.domain.models import Activity, Setting
from app.ingest import intervals

B = "https://intervals.icu/api/v1"


@respx.mock
def test_sync_imports_runs_once(client):  # noqa: F811
    _, e = client
    acts = [
        {
            "id": "i1",
            "type": "Run",
            "source": "GARMIN_CONNECT",
            "file_type": "gpx",
            "name": "Lungo",
        },
        {"id": "i2", "type": "Run", "source": "STRAVA"},  # stub: not served by the API
        {"id": "i3", "type": "Ride", "source": "UPLOAD", "file_type": "fit"},
        {"id": "i4", "type": "Run", "source": "UPLOAD", "file_type": "fit"},
    ]
    respx.get(f"{B}/athlete/0/activities").respond(200, json=acts)
    f1 = respx.get(f"{B}/activity/i1/file").respond(200, content=gzip.compress(_gpx()))
    respx.get(f"{B}/activity/i4/file").respond(500)
    http = httpx.Client(base_url="https://intervals.icu")
    err = intervals.sync(e, http=http)
    assert err and err.startswith("1 failed") and "i4" in err
    with Session(e) as s:
        assert s.scalars(select(Activity.name)).all() == ["Lungo"]
        assert s.get(Setting, intervals.SEEN_KEY).value == ["i1"]
    intervals.sync(e, http=httpx.Client(base_url="https://intervals.icu"))
    assert f1.call_count == 1  # already imported: not downloaded again


def test_sync_endpoint_needs_key(client, monkeypatch):  # noqa: F811
    monkeypatch.setattr(get_settings(), "INTERVALS_API_KEY", "")  # ignore a local .env key
    c, _ = client
    assert c.get("/api/intervals/status").json() == {"enabled": False}
    h = {"X-Corsa": "1", "Content-Type": "application/json"}
    assert c.post("/api/intervals/sync", headers=h).status_code == 409
