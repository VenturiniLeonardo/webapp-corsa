from datetime import UTC, datetime

import httpx
import pytest
import respx
from sqlalchemy.orm import Session

from app.api.strava_auth import TOKEN_URL
from app.core.config import get_settings
from app.core.db import make_engine
from app.domain.models import Base, ProviderAccount
from app.ingest.strava_client import (
    RateLimitWaitException,
    StravaAuthError,
    StravaClient,
    next_midnight,
    next_quarter,
)

NOW = datetime(2026, 9, 30, 10, 7, 30, tzinfo=UTC)
BASE = "https://www.strava.com/api/v3"


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
    monkeypatch.delenv("STRAVA_API_BASE", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def engine(tmp_path):
    e = make_engine(f"sqlite:///{tmp_path / 's.db'}")
    Base.metadata.create_all(e)
    yield e
    e.dispose()


def seed(engine, expires_at):
    with Session(engine) as s, s.begin():
        s.add(
            ProviderAccount(
                provider="strava",
                access_token="old_at",
                refresh_token="old_rt",
                expires_at=expires_at,
            )
        )


def account(engine):
    with Session(engine) as s:
        return s.get(ProviderAccount, "strava")


def client(engine, now=NOW):
    return StravaClient(engine, http=httpx.Client(), now=lambda: now)


FRESH = int(NOW.timestamp()) + 3600


def test_window_math():
    assert next_quarter(NOW) == datetime(2026, 9, 30, 10, 15, tzinfo=UTC)
    assert next_quarter(datetime(2026, 9, 30, 23, 50, tzinfo=UTC)) == datetime(
        2026, 10, 1, 0, 0, tzinfo=UTC
    )
    assert next_midnight(NOW) == datetime(2026, 10, 1, tzinfo=UTC)


@respx.mock
def test_refresh_rotates_and_persists_before_call(engine):
    seed(engine, expires_at=int(NOW.timestamp()) + 120)  # within 5 min margin
    seen = {}

    def token(req):
        seen["body"] = req.content.decode()
        return httpx.Response(
            200, json={"access_token": "new_at", "refresh_token": "new_rt", "expires_at": FRESH}
        )

    def act(req):
        # Rotated tokens must already be committed when the API call goes out.
        acc = account(engine)
        seen["db"] = (acc.access_token, acc.refresh_token)
        seen["auth"] = req.headers["Authorization"]
        return httpx.Response(200, json={"id": 1})

    respx.post(TOKEN_URL).mock(side_effect=token)
    respx.get(f"{BASE}/activities/1").mock(side_effect=act)

    assert client(engine).get_activity_detail(1) == {"id": 1}
    assert "grant_type=refresh_token" in seen["body"] and "refresh_token=old_rt" in seen["body"]
    assert seen["db"] == ("new_at", "new_rt")
    assert seen["auth"] == "Bearer new_at"
    acc = account(engine)
    assert (acc.access_token, acc.refresh_token, acc.expires_at) == ("new_at", "new_rt", FRESH)


@respx.mock
def test_no_refresh_when_token_valid(engine):
    seed(engine, FRESH)
    tok = respx.post(TOKEN_URL)
    respx.get(f"{BASE}/athlete/activities", params={"page": 1, "per_page": 200}).respond(
        200, json=[{"id": 9}]
    )
    assert client(engine).get_athlete_activities() == [{"id": 9}]
    assert not tok.called


@respx.mock
def test_refresh_rejected_marks_reauth(engine):
    seed(engine, 0)
    respx.post(TOKEN_URL).respond(400, json={"message": "Bad Request"})
    with pytest.raises(StravaAuthError):
        client(engine).get_activity_detail(1)
    acc = account(engine)
    assert acc.status == "reauth_required" and acc.refresh_token == "old_rt"


@respx.mock
def test_429_quarter_backoff(engine):
    seed(engine, FRESH)
    respx.get(f"{BASE}/activities/1").respond(
        429,
        headers={"X-RateLimit-Limit": "200,2000", "X-RateLimit-Usage": "201,500"},
    )
    with pytest.raises(RateLimitWaitException) as e:
        client(engine).get_activity_detail(1)
    assert e.value.resume_at == datetime(2026, 9, 30, 10, 15, tzinfo=UTC)


@respx.mock
def test_429_daily_backoff(engine):
    seed(engine, FRESH)
    respx.get(f"{BASE}/activities/1").respond(
        429,
        headers={"X-ReadRateLimit-Limit": "100,1000", "X-ReadRateLimit-Usage": "40,1000"},
    )
    with pytest.raises(RateLimitWaitException) as e:
        client(engine).get_activity_detail(1)
    assert e.value.resume_at == datetime(2026, 10, 1, tzinfo=UTC)


@respx.mock
def test_self_limit_blocks_before_request(engine):
    seed(engine, FRESH)
    route = respx.get(f"{BASE}/activities/1").respond(
        200, json={}, headers={"X-ReadRateLimit-Usage": "90,300"}
    )
    c = client(engine)
    c.get_activity_detail(1)  # header usage (incl. this call) hits the 90 cap
    with pytest.raises(RateLimitWaitException) as e:
        c.get_activity_detail(1)
    assert e.value.resume_at == datetime(2026, 9, 30, 10, 15, tzinfo=UTC)
    assert route.call_count == 1

    later = client(engine, now=datetime(2026, 9, 30, 10, 15, 1, tzinfo=UTC))
    later.used_15m, later.used_day, later._quarter = c.used_15m, c.used_day, c._quarter
    later._day = c._day
    later.get_activity_detail(1)  # new window resets the 15-min counter
    assert route.call_count == 2


@respx.mock
def test_daily_self_limit(engine):
    seed(engine, FRESH)
    c = client(engine)
    c._roll(NOW)
    c.used_day = 900
    with pytest.raises(RateLimitWaitException) as e:
        c.get_activity_detail(1)
    assert e.value.resume_at == datetime(2026, 10, 1, tzinfo=UTC)


@respx.mock
def test_configurable_base_url(engine, monkeypatch):
    monkeypatch.setenv("STRAVA_API_BASE", "https://api-v3.strava.com/")
    get_settings.cache_clear()
    seed(engine, FRESH)
    route = respx.get(
        "https://api-v3.strava.com/activities/5/streams",
        params={"keys": "time,heartrate", "key_by_type": "true"},
    ).respond(200, json={"time": {"data": [0, 1]}})
    assert client(engine).get_activity_streams(5, ["time", "heartrate"]) == {
        "time": {"data": [0, 1]}
    }
    assert route.called


@respx.mock
def test_revoke(engine):
    route = respx.post("https://www.strava.com/oauth/revoke").respond(200)
    assert client(engine).revoke_token("at") is True
    assert b"token=at" in route.calls.last.request.content
