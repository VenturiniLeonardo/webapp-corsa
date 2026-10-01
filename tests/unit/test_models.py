import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.db import make_engine
from app.domain.models import (
    Activity,
    ActivityTag,
    Base,
    BestEffort,
    Job,
    Lap,
    SourceRecord,
    Tag,
)


@pytest.fixture
def s(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 't.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def act(**kw):
    return Activity(
        **{
            "sport_type": "run",
            "start_time_utc": "2026-01-01T07:00:00+00:00",
            "local_date": "2026-01-01",
            **kw,
        }
    )


def add_act(s, **kw):
    a = act(**kw)
    s.add(a)
    s.flush()
    return a


def test_pragmas(s):
    c = s.connection()
    assert c.execute(text("PRAGMA foreign_keys")).scalar() == 1
    assert c.execute(text("PRAGMA journal_mode")).scalar() == "wal"
    assert c.execute(text("PRAGMA busy_timeout")).scalar() == 5000
    assert c.execute(text("PRAGMA synchronous")).scalar() == 1  # NORMAL


def test_no_users(s):
    insp = inspect(s.connection())
    assert "users" not in insp.get_table_names()
    for t in insp.get_table_names():
        assert "user_id" not in [c["name"] for c in insp.get_columns(t)]


@pytest.mark.parametrize(
    "kw",
    [{"sport_type": "swim"}, {"workout_type": "tempo"}, {"distance_m": -1}],
)
def test_activity_checks(s, kw):
    with pytest.raises(IntegrityError):
        add_act(s, **kw)


def test_activity_defaults(s):
    a = add_act(s)
    assert a.excluded_from_stats is False and a.created_at


def test_source_unique_and_checks(s):
    s.add(SourceRecord(source="strava", external_id="1"))
    s.flush()
    s.add(SourceRecord(source="strava", external_id="1"))
    with pytest.raises(IntegrityError):
        s.flush()
    s.rollback()
    s.add(SourceRecord(source="garmin", external_id="1"))
    with pytest.raises(IntegrityError):
        s.flush()
    s.rollback()
    s.add(SourceRecord(source="strava", external_id="2", status="imported"))
    with pytest.raises(IntegrityError):
        s.flush()


def test_source_same_id_different_source_ok(s):
    s.add_all(
        [
            SourceRecord(source="strava", external_id="1"),
            SourceRecord(source="file_fit", external_id="1"),
        ]
    )
    s.flush()


def test_foreign_keys_enforced(s):
    s.add(SourceRecord(source="strava", external_id="1", activity_id=999))
    with pytest.raises(IntegrityError):
        s.flush()
    s.rollback()
    s.add(act(primary_source_id=999))
    with pytest.raises(IntegrityError):
        s.flush()


def test_activity_source_cycle(s):
    a = add_act(s)
    sr = SourceRecord(source="strava", external_id="1", activity_id=a.id)
    s.add(sr)
    s.flush()
    a.primary_source_id = sr.id
    a.stream_source_id = sr.id
    s.flush()


def test_lap_unique_and_kind(s):
    a = add_act(s)
    s.add(Lap(activity_id=a.id, kind="split_km", idx=1))
    s.flush()
    s.add(Lap(activity_id=a.id, kind="split_km", idx=1))
    with pytest.raises(IntegrityError):
        s.flush()
    s.rollback()
    a = add_act(s)
    s.add(Lap(activity_id=a.id, kind="mile", idx=1))
    with pytest.raises(IntegrityError):
        s.flush()


def test_best_effort_unique(s):
    a = add_act(s)
    s.add(BestEffort(activity_id=a.id, distance_m=5000.0, elapsed_s=1500, algo_version=1))
    s.flush()
    s.add(BestEffort(activity_id=a.id, distance_m=5000.0, elapsed_s=1400, algo_version=1))
    with pytest.raises(IntegrityError):
        s.flush()


def test_tags_nocase_unique_and_cascade(s):
    s.add(Tag(name="Race"))
    s.flush()
    s.add(Tag(name="race"))
    with pytest.raises(IntegrityError):
        s.flush()
    s.rollback()
    a = add_act(s)
    t = Tag(name="x")
    s.add(t)
    s.flush()
    s.add(ActivityTag(activity_id=a.id, tag_id=t.id))
    s.flush()
    s.add(ActivityTag(activity_id=a.id, tag_id=t.id))
    with pytest.raises(IntegrityError):
        s.flush()
    s.rollback()


def test_job_status_check_and_defaults(s):
    j = Job(kind="strava_sync")
    s.add(j)
    s.flush()
    s.refresh(j)
    assert j.status == "queued" and j.attempts == 0
    s.add(Job(kind="k", status="succeeded"))
    with pytest.raises(IntegrityError):
        s.flush()
