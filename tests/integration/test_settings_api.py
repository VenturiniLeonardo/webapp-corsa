import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_session, make_engine
from app.domain.models import Base, Job, ProviderAccount, SourceRecord
from app.main import app

H = {"X-Corsa": "1", "Content-Type": "application/json"}


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for k, v in {
        "STRAVA_CLIENT_ID": "cid",
        "STRAVA_CLIENT_SECRET": "sec",
        "ALLOWED_LOGINS": "me",
        "DATABASE_URL": "sqlite://",
        "ENV": "dev",
        "AUTH_DEV_LOGIN": "me",
    }.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def engine(tmp_path):
    e = make_engine(f"sqlite:///{tmp_path / 'p.db'}")
    Base.metadata.create_all(e)
    yield e
    e.dispose()


@pytest.fixture
def client(engine):
    def override():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = override
    yield TestClient(app)
    app.dependency_overrides.clear()


def jobs(engine, kind):
    with Session(engine) as s:
        return [j.status for j in s.query(Job).filter_by(kind=kind)]


def test_zones_must_be_strictly_ascending(client, engine):
    for z in ([130, 130, 160, 175], [150, 140, 160, 175], [130, 145, 160], [0, 145, 160, 175]):
        assert client.put("/api/settings", json={"hr_zones": z}, headers=H).status_code == 422
    assert client.get("/api/settings").json()["hr_zones"] is None
    assert jobs(engine, "recompute") == []


def test_zone_change_enqueues_recompute_once(client, engine):
    r = client.put(
        "/api/settings", json={"hr_zones": [130, 145, 160, 175], "hr_max": 190}, headers=H
    )
    assert r.status_code == 200
    body = r.json()
    assert (
        body["settings"]["hr_zones"] == [130, 145, 160, 175] and body["settings"]["hr_max"] == 190
    )
    assert body["recompute_job_id"] and jobs(engine, "recompute") == ["queued"]
    # unchanged zones / hr_max only -> no new job
    r = client.put("/api/settings", json={"hr_zones": [130, 145, 160, 175]}, headers=H)
    assert r.json()["recompute_job_id"] is None
    r = client.put("/api/settings", json={"hr_rest": 50}, headers=H)
    assert r.json()["recompute_job_id"] is None and len(jobs(engine, "recompute")) == 1
    assert client.get("/api/settings").json()["hr_rest"] == 50


def test_put_requires_csrf_headers(client):
    assert client.put("/api/settings", json={"hr_rest": 50}).status_code == 403


def test_sync_dedup_and_jobs(client, engine):
    a = client.post("/api/sync", headers=H).json()["job_id"]
    b = client.post("/api/sync", headers=H).json()["job_id"]
    assert a == b and jobs(engine, "strava_sync") == ["queued"]
    lst = client.get("/api/jobs").json()
    assert [j["id"] for j in lst] == [a] and lst[0]["status"] == "queued"
    assert client.get("/api/jobs", params={"status": "failed"}).json() == []
    d = client.get(f"/api/jobs/{a}").json()
    assert d["job"]["kind"] == "strava_sync" and d["failed_records"] == []
    assert client.get("/api/jobs/999").status_code == 404


def test_strava_status(client, engine):
    assert client.get("/api/strava/status").json()["connected"] is False
    with Session(engine) as s, s.begin():
        s.add(
            ProviderAccount(
                provider="strava",
                athlete_id="42",
                refresh_token="rt",
                scopes="activity:read_all",
                status="active",
                last_sync_at="2026-09-30T10:00:00+00:00",
            )
        )
    r = client.get("/api/strava/status").json()
    assert r["connected"] and r["athlete_id"] == "42" and r["rate_usage"]["limit_day"] == 900
    assert r["last_sync_at"] == "2026-09-30T10:00:00+00:00"


def test_retry_failed_record(client, engine):
    with Session(engine) as s, s.begin():
        s.add(Job(kind="strava_sync", status="done", params={}))
        s.add(
            SourceRecord(source="strava", external_id="1", status="error", error="boom", job_id=1)
        )
        s.add(SourceRecord(source="strava", external_id="2", status="mapped"))
    d = client.get("/api/jobs/1").json()
    assert d["failed_records"] == [{"id": 1, "external_id": "1", "error": "boom"}]
    r = client.post("/api/source-records/1/retry", headers=H)
    assert r.status_code == 200 and jobs(engine, "strava_backfill") == ["queued"]
    assert client.post("/api/source-records/2/retry", headers=H).status_code == 409
    assert client.post("/api/source-records/9/retry", headers=H).status_code == 404
