import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_session, make_engine
from app.domain.models import Base, Job, SourceRecord
from app.main import app

H = {"X-Corsa": "1", "Content-Type": "application/json"}


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for k, v in {
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


def test_jobs_list_and_detail(client, engine):
    with Session(engine) as s, s.begin():
        s.add(Job(kind="recompute", status="done", params={}))
        s.add(
            SourceRecord(source="file_fit", external_id="1", status="error", error="boom", job_id=1)
        )
    lst = client.get("/api/jobs").json()
    assert [j["id"] for j in lst] == [1] and lst[0]["status"] == "done"
    assert client.get("/api/jobs", params={"status": "failed"}).json() == []
    d = client.get("/api/jobs/1").json()
    assert d["job"]["kind"] == "recompute"
    assert d["failed_records"] == [{"id": 1, "external_id": "1", "error": "boom"}]
    assert client.get("/api/jobs/999").status_code == 404
