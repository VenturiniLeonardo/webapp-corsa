import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import app

BASE = {
    "STRAVA_CLIENT_ID": "x",
    "STRAVA_CLIENT_SECRET": "x",
    "STRAVA_API_BASE": "https://www.strava.com/api/v3",
    "ALLOWED_LOGINS": "me@x, you@x",
    "DATABASE_URL": "sqlite://",
    "AUTH_DEV_LOGIN": "dev_user",
}


def make(monkeypatch: pytest.MonkeyPatch, env: str) -> TestClient:
    for k, v in {**BASE, "ENV": env}.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    return TestClient(app)


def test_prod_missing_header_401(monkeypatch: pytest.MonkeyPatch) -> None:
    c = make(monkeypatch, "prod")
    assert c.get("/api/x").status_code == 401
    assert c.get("/api/x", headers={"Tailscale-User-Login": "evil@x"}).status_code == 401
    assert c.get("/api/x", headers={"Tailscale-User-Login": "me@x"}).status_code == 404


def test_post_without_csrf_header_403(monkeypatch: pytest.MonkeyPatch) -> None:
    c = make(monkeypatch, "prod")
    h = {"Tailscale-User-Login": "me@x"}
    assert c.post("/api/x", headers=h, json={}).status_code == 403
    assert c.post("/api/x", headers={**h, "X-Corsa": "1"}, content="a").status_code == 403
    assert (
        c.post("/api/x", headers={**h, "X-Corsa": "1"}, json={}).status_code == 405
    )  # passed middleware
    assert c.post("/api/strava/callback").status_code == 405  # exempt


def test_healthz_ok_with_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    r = make(monkeypatch, "prod").get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "db": True, "last_sync_age_s": None, "failed_jobs_24h": 0}
    assert "tiles.openfreemap.org" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def test_dev_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    c = make(monkeypatch, "dev")
    assert c.get("/api/x").status_code == 404  # authenticated via AUTH_DEV_LOGIN
