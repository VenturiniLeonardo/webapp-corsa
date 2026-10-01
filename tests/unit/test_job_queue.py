import threading
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.core.db import make_engine
from app.domain.models import Base
from app.worker.queue import MAX_ATTEMPTS, JobQueue, _iso


@pytest.fixture
def engine(tmp_path):
    e = make_engine(f"sqlite:///{tmp_path / 'q.db'}")
    Base.metadata.create_all(e)
    yield e
    e.dispose()


@pytest.fixture
def q(engine):
    return JobQueue(engine)


def _set(engine, job_id, **cols):
    sets = ", ".join(f"{k}=:{k}" for k in cols)
    with engine.begin() as c:
        c.execute(text(f"UPDATE jobs SET {sets} WHERE id=:id"), {**cols, "id": job_id})


def _get(engine, job_id):
    with engine.connect() as c:
        return c.execute(text("SELECT * FROM jobs WHERE id=:id"), {"id": job_id}).one()


def test_enqueue_is_idempotent_for_pending(q):
    a = q.enqueue("strava_sync", {"b": 1, "a": 2})
    assert q.enqueue("strava_sync", {"a": 2, "b": 1}) == a
    assert q.enqueue("strava_sync", {"a": 3}) != a
    assert q.enqueue("strava_backfill", {"a": 2, "b": 1}) != a
    q.claim_job()
    assert q.enqueue("strava_sync", {"b": 1, "a": 2}) == a  # running still counts
    q.finish_job(a, "done")
    assert q.enqueue("strava_sync", {"b": 1, "a": 2}) != a


def test_claim_fifo_and_params_roundtrip(q):
    a = q.enqueue("k", {"x": [1, 2]})
    b = q.enqueue("k", {"x": 3})
    j = q.claim_job()
    assert j.id == a and j.status == "running" and j.params == {"x": [1, 2]}
    assert j.attempts == 1 and j.started_at == j.heartbeat_at
    assert q.claim_job().id == b
    assert q.claim_job() is None


def test_atomic_claim_no_double_claims(engine):
    q = JobQueue(engine)
    n = 40
    for i in range(n):
        q.enqueue("k", {"i": i})
    claimed: list[int] = []
    lock = threading.Lock()
    start = threading.Barrier(8)

    def worker():
        wq = JobQueue(engine)
        start.wait()
        while (j := wq.claim_job()) is not None:
            with lock:
                claimed.append(j.id)

    ts = [threading.Thread(target=worker) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(claimed) == n
    assert len(set(claimed)) == n


def test_not_before_delays_execution(q):
    future = datetime.now(UTC) + timedelta(hours=1)
    delayed = q.enqueue("k", {"d": 1}, not_before=future)
    assert q.claim_job() is None
    ready = q.enqueue("k", {"d": 2}, not_before=datetime.now(UTC) - timedelta(seconds=1))
    assert q.claim_job().id == ready  # later id, but earlier one is not yet due
    assert q.claim_job() is None
    _set(q.engine, delayed, not_before=_iso(datetime.now(UTC) - timedelta(seconds=1)))
    assert q.claim_job().id == delayed


def test_heartbeat_and_finish(q):
    jid = q.enqueue("k", {})
    q.claim_job()
    _set(q.engine, jid, heartbeat_at="2000-01-01T00:00:00+00:00")
    q.heartbeat(jid)
    assert _get(q.engine, jid).heartbeat_at > "2000"
    assert _get(q.engine, jid).heartbeat_at != "2000-01-01T00:00:00+00:00"
    q.finish_job(jid, "failed", "boom")
    row = _get(q.engine, jid)
    assert (row.status, row.error) == ("failed", "boom") and row.finished_at
    with pytest.raises(ValueError):
        q.finish_job(jid, "running")


def test_recover_stale_jobs(q):
    old = _iso(datetime.now(UTC) - timedelta(minutes=6))
    stale = q.enqueue("k", {"s": 1})
    fresh = q.enqueue("k", {"s": 2})
    exhausted = q.enqueue("k", {"s": 3})
    for _ in range(3):
        q.claim_job()
    _set(q.engine, stale, heartbeat_at=old)
    _set(q.engine, exhausted, heartbeat_at=old, attempts=MAX_ATTEMPTS)

    assert q.recover_stale_jobs() == 2
    assert _get(q.engine, stale).status == "queued"
    assert _get(q.engine, fresh).status == "running"
    row = _get(q.engine, exhausted)
    assert row.status == "failed" and row.finished_at and row.error

    j = q.claim_job()
    assert j.id == stale and j.attempts == 2
