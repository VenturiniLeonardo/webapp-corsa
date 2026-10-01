import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import make_engine
from app.domain.models import (
    Activity,
    ActivityMetrics,
    Base,
    BestEffort,
    Job,
    Lap,
    ProviderAccount,
    Setting,
    SourceRecord,
    Stream,
)
from app.ingest.strava_client import StravaClient
from app.worker.queue import JobQueue
from app.worker.runner import Runner, Scheduler

BASE = "https://www.strava.com/api/v3"
T0 = "2026-09-01T07:00:00Z"


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
    monkeypatch.delenv("HEALTHCHECK_URL_SYNC", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def engine(tmp_path):
    e = make_engine(f"sqlite:///{tmp_path / 'p.db'}")
    Base.metadata.create_all(e)
    with Session(e) as s, s.begin():
        s.add(
            ProviderAccount(
                provider="strava",
                access_token="at",
                refresh_token="rt",
                expires_at=int(time.time()) + 3600,
                status="active",
            )
        )
        s.add(Setting(key="hr_zones", value=[130, 145, 160, 175]))
    yield e
    e.dispose()


@pytest.fixture
def q(engine):
    return JobQueue(engine)


@pytest.fixture
def runner(engine, q):
    return Runner(engine, StravaClient(engine, http=httpx.Client()), q)


def summary(i, sport="Run", start=T0, **kw):
    return {
        "id": i,
        "sport_type": sport,
        "name": f"Run {i}",
        "start_date": start,
        "distance": 3000.0,
        **kw,
    }


def detail(i, start=T0, **kw):
    return {
        **summary(i, start=start),
        "timezone": "(GMT+01:00) Europe/Rome",
        "moving_time": 900,
        "elapsed_time": 910,
        "average_heartrate": 150,
        "laps": [
            {"start_date": start, "elapsed_time": 900, "moving_time": 900, "distance": 3000.0}
        ],
        **kw,
    }


def streams(n=91):
    t = [10 * i for i in range(n)]
    return {
        "time": {"data": t},
        "distance": {"data": [x * 10 / 3 for x in t]},
        "velocity_smooth": {"data": [3.3] * n},
        "heartrate": {"data": [150] * n},
    }


def mock_activity(i, start=T0, **kw):
    respx.get(f"{BASE}/activities/{i}").respond(200, json=detail(i, start, **kw))
    respx.get(f"{BASE}/activities/{i}/streams").respond(200, json=streams())


def run_job(q, runner, kind):
    jid = q.enqueue(kind, {})
    job = q.claim_job()
    assert job and job.id == jid
    runner.execute(job)
    return jid


def job_row(engine, jid) -> Job:
    with Session(engine) as s:
        return s.get(Job, jid)


def count(engine, model):
    with Session(engine) as s:
        return s.scalar(select(func.count()).select_from(model))


@respx.mock
def test_backfill_imports_atomically_and_idempotently(engine, q, runner):
    respx.get(f"{BASE}/athlete/activities").respond(
        200,
        json=[
            summary(1),
            summary(2, start="2026-09-01T07:00:30Z"),  # same run, second Strava activity
            summary(3, start="2026-09-02T07:00:00Z"),
            summary(4, sport="Ride", start="2026-09-03T07:00:00Z"),
        ],
    )
    mock_activity(1)
    mock_activity(2, start="2026-09-01T07:00:30Z")
    mock_activity(3, start="2026-09-02T07:00:00Z")

    jid = run_job(q, runner, "strava_backfill")

    j = job_row(engine, jid)
    assert (j.status, j.error, j.progress_done, j.progress_total) == ("done", None, 4, 4)
    with Session(engine) as s:
        assert {r.external_id: r.status for r in s.scalars(select(SourceRecord))} == {
            "1": "mapped",
            "2": "mapped",
            "3": "mapped",
            "4": "skipped",
        }
        acts = {a.name: a for a in s.scalars(select(Activity))}
        assert len(acts) == 3
        assert (
            acts["Run 2"].duplicate_of_id == acts["Run 1"].id and acts["Run 2"].excluded_from_stats
        )
        assert not acts["Run 1"].excluded_from_stats and acts["Run 1"].local_date == "2026-09-01"
        m = s.get(ActivityMetrics, acts["Run 1"].id)
        assert m.time_in_zones_s == [0, 0, 900, 0, 0] and m.zones_hash
        assert s.scalar(select(ProviderAccount.sync_cursor)) == int(
            datetime(2026, 9, 3, 7, tzinfo=UTC).timestamp()
        )
    assert count(engine, Stream) == 3 and count(engine, BestEffort) > 0
    with Session(engine) as s:  # 3 km -> 3 split laps + 1 device lap per activity
        assert s.scalar(select(func.count()).select_from(Lap).where(Lap.kind == "split_km")) == 9

    calls = len(respx.calls)
    jid2 = run_job(q, runner, "strava_backfill")  # only the list call: nothing new
    assert job_row(engine, jid2).status == "done"
    assert len(respx.calls) == calls + 1
    assert count(engine, Activity) == 3


@respx.mock
def test_failed_activity_leaves_no_partial_rows(engine, q, runner):
    respx.get(f"{BASE}/athlete/activities").respond(
        200, json=[summary(1), summary(2, start="2026-09-02T07:00:00Z")]
    )
    respx.get(f"{BASE}/activities/1").respond(200, json=detail(1))
    bad = streams()
    bad["distance"]["data"].pop()  # length mismatch -> codec raises after the activity row is added
    respx.get(f"{BASE}/activities/1/streams").respond(200, json=bad)
    mock_activity(2, start="2026-09-02T07:00:00Z")

    jid = run_job(q, runner, "strava_backfill")

    j = job_row(engine, jid)
    assert j.status == "done" and j.error == "1 activities failed"
    with Session(engine) as s:
        assert [a.name for a in s.scalars(select(Activity))] == ["Run 2"]
        bad_rec = s.scalar(select(SourceRecord).where(SourceRecord.external_id == "1"))
        assert (
            bad_rec.status == "error"
            and "mismatch" in bad_rec.error
            and bad_rec.activity_id is None
        )
    assert (
        count(engine, Stream) == 1 and count(engine, Lap) == 4
    )  # run 2 only (1 device + 3 splits)


@respx.mock
def test_reconcile_marks_upstream_deletions_without_deleting(engine, q, runner):
    respx.get(f"{BASE}/athlete/activities").respond(
        200, json=[summary(1), summary(2, start="2026-09-02T07:00:00Z")]
    )
    mock_activity(1)
    mock_activity(2, start="2026-09-02T07:00:00Z")
    run_job(q, runner, "strava_backfill")

    respx.get(f"{BASE}/athlete/activities").respond(
        200, json=[summary(2, start="2026-09-02T07:00:00Z")]
    )
    jid = run_job(q, runner, "strava_reconcile")

    assert job_row(engine, jid).status == "done"
    with Session(engine) as s:
        a = {x.name: x for x in s.scalars(select(Activity))}
        assert len(a) == 2  # nothing hard-deleted
        assert a["Run 1"].upstream_deleted_at and a["Run 1"].excluded_from_stats
        assert a["Run 2"].upstream_deleted_at is None and not a["Run 2"].excluded_from_stats
        assert s.scalar(select(ProviderAccount.last_reconcile_at))
    assert count(engine, Stream) == 2

    respx.get(f"{BASE}/athlete/activities").respond(200, json=[])  # glitch: must not wipe the rest
    jid = run_job(q, runner, "strava_reconcile")
    assert job_row(engine, jid).status == "failed"
    with Session(engine) as s:
        assert (
            s.scalar(select(Activity).where(Activity.name == "Run 2")).upstream_deleted_at is None
        )


@respx.mock
def test_sync_cursor_window_changed_summary_and_ping(engine, q, runner, monkeypatch):
    monkeypatch.setenv("HEALTHCHECK_URL_SYNC", "https://hc.example/ping")
    get_settings.cache_clear()
    ping = respx.get("https://hc.example/ping").respond(200)
    lst = respx.get(f"{BASE}/athlete/activities").respond(200, json=[summary(1)])
    mock_activity(1)
    run_job(q, runner, "strava_backfill")
    assert not ping.called

    # Title edited upstream: summary changed -> detail refetched and activity updated.
    lst.respond(200, json=[{**summary(1), "name": "Renamed"}])
    detail_route = respx.get(f"{BASE}/activities/1").respond(
        200, json={**detail(1), "name": "Renamed"}
    )
    before = detail_route.call_count  # respx reuses the route: includes the backfill call
    jid = run_job(q, runner, "strava_sync")

    assert job_row(engine, jid).status == "done" and ping.call_count == 1
    assert detail_route.call_count == before + 1
    sent = lst.calls.last.request.url.params
    # cursor is within 30 days, so the 30-day window reaches further back
    assert abs(int(sent["after"]) - (time.time() - 30 * 86400)) < 120
    with Session(engine) as s:
        assert s.scalar(select(Activity.name)) == "Renamed"
        assert s.scalar(select(ProviderAccount.last_sync_at))
    assert count(engine, Activity) == 1

    run_job(q, runner, "strava_sync")  # unchanged summary: no detail refetch
    assert detail_route.call_count == before + 1

    old = int(
        datetime(2026, 1, 1, tzinfo=UTC).timestamp()
    )  # stale cursor: cursor-1h reaches further
    with Session(engine) as s, s.begin():
        s.get(ProviderAccount, "strava").sync_cursor = old
    run_job(q, runner, "strava_sync")
    assert int(lst.calls.last.request.url.params["after"]) == old - 3600


@respx.mock
def test_rate_limit_requeues_with_not_before(engine, q, runner):
    respx.get(f"{BASE}/athlete/activities").respond(200, json=[summary(1)])
    respx.get(f"{BASE}/activities/1").respond(429, headers={"X-RateLimit-Usage": "5,5"})

    jid = run_job(q, runner, "strava_backfill")

    j = job_row(engine, jid)
    assert j.status == "queued" and j.not_before and j.attempts == 0 and j.finished_at is None
    assert q.claim_job() is None  # not before the next quarter hour
    assert count(engine, Activity) == 0


@respx.mock
def test_network_error_retries_with_backoff_then_fails(engine, q, runner):
    respx.get(f"{BASE}/athlete/activities").mock(side_effect=httpx.ConnectError("down"))

    jid = run_job(q, runner, "strava_backfill")
    j = job_row(engine, jid)
    assert j.status == "queued" and j.attempts == 1
    assert datetime.fromisoformat(j.not_before) > datetime.now(UTC) + timedelta(seconds=5)

    with engine.begin() as c:  # exhaust the retries
        c.exec_driver_sql("UPDATE jobs SET attempts=4, status='running' WHERE id=?", (jid,))
    runner.execute(job_row(engine, jid))
    assert job_row(engine, jid).status == "failed"


def test_scheduler_enqueues_periodically_and_dedups(engine, q):
    t = datetime(2026, 9, 30, 10, tzinfo=UTC)
    clock = [t]
    sched = Scheduler(engine, q, now=lambda: clock[0])

    def kinds():
        with Session(engine) as s:
            return sorted(s.scalars(select(Job.kind)))

    sched.tick()
    assert kinds() == ["strava_reconcile", "strava_sync"]
    clock[0] = t + timedelta(minutes=10)
    sched.tick()
    assert len(kinds()) == 2
    for _ in range(2):
        q.finish_job(q.claim_job().id, "done")
    clock[0] = t + timedelta(minutes=31)
    sched.tick()
    assert kinds() == ["strava_reconcile", "strava_sync", "strava_sync"]  # reconcile is weekly
    clock[0] = t + timedelta(days=7, minutes=1)
    sched.tick()
    assert kinds().count("strava_reconcile") == 2


def test_scheduler_skips_when_not_connected(engine, q):
    with Session(engine) as s, s.begin():
        s.get(ProviderAccount, "strava").status = "reauth_required"
    Scheduler(engine, q).tick()
    assert q.claim_job() is None
