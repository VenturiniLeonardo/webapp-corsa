import random
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta
from itertools import pairwise
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api import stats
from app.api.stats import theil_sen
from app.core.config import get_settings
from app.core.db import get_session, make_engine
from app.domain.models import Activity, ActivityMetrics, Base, BestEffort
from app.main import app

TODAY = date(2026, 9, 30)  # a Wednesday
ROME = "Europe/Rome"


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for k, v in {
        "ALLOWED_LOGINS": "me",
        "DATABASE_URL": "sqlite://",
        "ENV": "dev",
        "AUTH_DEV_LOGIN": "me",
    }.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(stats, "_today", lambda: TODAY)
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


def add(s, start_utc, dist=10000.0, mov=3000, tz=ROME, **kw) -> Activity:
    """Activity whose local_date is derived from start/tz exactly like the Strava mapper does."""
    start = datetime.fromisoformat(start_utc)
    a = Activity(
        **{
            "sport_type": "run",
            "name": start_utc,
            "start_time_utc": start.isoformat(timespec="seconds"),
            "timezone": tz,
            "local_date": start.astimezone(ZoneInfo(tz)).date().isoformat(),
            "distance_m": dist,
            "moving_s": mov,
            "elapsed_s": mov,
            "avg_hr": 150.0,
            "elev_gain_m": 0.0,
            "is_indoor": False,
            **kw,
        }
    )
    s.add(a)
    s.flush()
    return a


def metrics(s, a, **kw):
    s.add(ActivityMetrics(activity_id=a.id, algo_version=1, **kw))


def get(client, path, **params):
    r = client.get(path, params=params)
    assert r.status_code == 200, r.text
    return r.json()


# --- ISO weeks on local_date ----------------------------------------------------------
def test_iso_week_boundary_uses_local_date(engine, client):
    with Session(engine) as s, s.begin():
        sun = add(s, "2026-09-06T21:30:00+00:00", 10000, 3000)  # Sun 23:30 Rome
        mon = add(s, "2026-09-06T22:30:00+00:00", 5000, 1500)  # Mon 00:30 Rome, Sunday in UTC
        assert (sun.local_date, mon.local_date) == ("2026-09-06", "2026-09-07")

    items = get(client, "/api/stats/volume", bucket="week", from_date="2026-08-31")["items"]
    used = {i["start"]: (i["run_count"], i["distance_m"]) for i in items if i["run_count"]}
    assert used == {"2026-08-31": (1, 10000), "2026-09-07": (1, 5000)}
    assert [i["start"] for i in items][:3] == ["2026-08-31", "2026-09-07", "2026-09-14"]

    top = get(client, "/api/stats/top-weeks", limit=5)
    assert [(w["start"], w["end"]) for w in top] == [
        ("2026-08-31", "2026-09-06"),
        ("2026-09-07", "2026-09-13"),
    ]


def test_volume_matches_python_reference_with_gaps_and_partials(engine, client):
    rng = random.Random(7)
    ref_w, ref_m = defaultdict(list), defaultdict(list)
    with Session(engine) as s, s.begin():
        for _ in range(45):
            day = date(2026, 6, 1) + timedelta(days=rng.randrange(0, 120))  # 06-01 .. 09-28
            dist, mov = rng.randrange(3000, 25000, 100), rng.randrange(900, 9000, 10)
            a = add(
                s,
                f"{day}T{rng.randrange(0, 23):02d}:15:00+00:00",
                dist,
                mov,
                elev_gain_m=float(rng.randrange(0, 300)),
            )
            d = date.fromisoformat(a.local_date)
            row = (dist, mov, a.elev_gain_m)
            ref_w[d - timedelta(days=d.weekday())].append(row)
            ref_m[d.replace(day=1)].append(row)

    def check(bucket, ref):
        items = get(client, "/api/stats/volume", bucket=bucket, from_date="2026-06-01")["items"]
        got = {date.fromisoformat(i["start"]): i for i in items if i["run_count"]}
        assert set(got) == set(ref)
        for start, rows in ref.items():
            g = got[start]
            assert g["run_count"] == len(rows)
            assert g["distance_m"] == sum(r[0] for r in rows)
            assert g["moving_s"] == sum(r[1] for r in rows)
            assert g["elev_gain_m"] == sum(r[2] for r in rows)
            assert g["longest_run_m"] == max(r[0] for r in rows)
            assert g["weighted_pace_s_per_km"] == pytest.approx(
                sum(r[1] for r in rows) / (sum(r[0] for r in rows) / 1000)
            )
        return items

    weeks = check("week", ref_w)
    starts = [date.fromisoformat(i["start"]) for i in weeks]
    assert starts[0] == date(2026, 6, 1) and starts[-1] == date(2026, 9, 28)
    assert all(b - a == timedelta(days=7) for a, b in pairwise(starts))  # gaps filled
    assert [i["partial"] for i in weeks] == [False] * (len(weeks) - 1) + [
        True
    ]  # week of 09-28 is current

    months = check("month", ref_m)
    assert [i["start"] for i in months] == ["2026-06-01", "2026-07-01", "2026-08-01", "2026-09-01"]
    assert [i["partial"] for i in months] == [False, False, False, True]

    # a scope that cuts the first bucket marks it partial
    cut = get(
        client, "/api/stats/volume", bucket="week", from_date="2026-06-03", to_date="2026-06-20"
    )
    assert [(i["start"], i["partial"]) for i in cut["items"]] == [
        ("2026-06-01", True),
        ("2026-06-08", False),
        ("2026-06-15", True),  # ends 06-21, scope ends 06-20
    ]

    top = get(client, "/api/stats/top-weeks", limit=3)
    want = sorted(ref_w, key=lambda w: (-sum(r[0] for r in ref_w[w]), -w.toordinal()))[:3]
    assert [date.fromisoformat(w["start"]) for w in top] == want


# --- summary / weighted pace ------------------------------------------------------------
def test_summary_weighted_pace_and_equal_previous_period(engine, client):
    with Session(engine) as s, s.begin():
        add(s, "2026-09-20T07:00:00+00:00", 10000, 3000)  # 300 s/km
        add(s, "2026-09-25T07:00:00+00:00", 5000, 2000)  # 400 s/km
        add(s, "2026-09-10T07:00:00+00:00", 20000, 6000)  # previous period
        add(s, "2026-09-22T07:00:00+00:00", 90000, 9000, excluded_from_stats=True)
        add(s, "2026-09-23T07:00:00+00:00", 90000, 9000, duplicate_of_id=1)
        add(s, "2026-09-24T07:00:00+00:00", 3000, 1200, sport_type="treadmill", is_indoor=True)

    d = get(client, "/api/stats/summary", from_date="2026-09-17", to_date="2026-09-30")
    assert d["period"] == {"from_date": "2026-09-17", "to_date": "2026-09-30", "days": 14}
    assert d["previous_period"] == {"from_date": "2026-09-03", "to_date": "2026-09-16", "days": 14}
    cur, prev = d["current"], d["previous"]
    assert (cur["run_count"], cur["distance_m"], cur["moving_s"]) == (2, 15000, 5000)
    assert cur["weighted_pace_s_per_km"] == pytest.approx(
        5000 / 15
    )  # 333.3, not mean(300, 400) = 350
    assert cur["avg_weekly_distance_m"] == 7500 and cur["longest_run_m"] == 10000
    assert (prev["run_count"], prev["distance_m"], prev["avg_weekly_distance_m"]) == (
        1,
        20000,
        10000,
    )
    assert prev["weighted_pace_s_per_km"] == 300 and prev["longest_run_m"] == 20000

    # indoor runs only count on request
    d = get(client, "/api/stats/summary", from_date="2026-09-17", include_indoor="true")
    assert d["current"]["run_count"] == 3 and d["current"]["distance_m"] == 18000

    # a period still in progress is compared over the same elapsed days
    d = get(client, "/api/stats/summary", from_date="2026-09-21", to_date="2026-10-15")
    assert d["period"] == {"from_date": "2026-09-21", "to_date": "2026-09-30", "days": 10}
    assert d["previous_period"]["from_date"] == "2026-09-11" and d["previous_period"]["days"] == 10

    # default period = last 28 days
    d = get(client, "/api/stats/summary")
    assert (d["period"]["from_date"], d["period"]["days"]) == ("2026-09-03", 28)

    empty = get(client, "/api/stats/summary", from_date="2026-01-01", to_date="2026-01-07")
    assert empty["current"]["weighted_pace_s_per_km"] is None and empty["current"]["run_count"] == 0


# --- trends -------------------------------------------------------------------------------
def seed_steady(s, n):
    """n steady runs on consecutive days: pace 300-2i s/km, hr 150+i, ef 1.0+0.01i."""
    for i in range(n):
        a = add(s, f"2026-09-{i + 1:02d}T07:00:00+00:00", 10000, 3000 - 20 * i, avg_hr=150.0 + i)
        metrics(s, a, is_steady=True, efficiency_factor=1.0 + 0.01 * i)
    noise = add(s, "2026-09-20T07:00:00+00:00", 10000, 100, avg_hr=99.0)  # not steady
    metrics(s, noise, is_steady=False)
    excl = add(s, "2026-09-21T07:00:00+00:00", 10000, 100, excluded_from_stats=True)
    metrics(s, excl, is_steady=True)


@pytest.mark.parametrize("n", [0, 7])
def test_trend_suppressed_below_min_n(engine, client, n):
    with Session(engine) as s, s.begin():
        seed_steady(s, n)
    for metric in ("pace", "hr", "ef"):
        d = get(client, "/api/stats/trends", metric=metric)
        assert d["n"] == n and len(d["points"]) == n
        assert d["trend"] is None


def test_trend_theil_sen_and_rolling_median(engine, client):
    with Session(engine) as s, s.begin():
        seed_steady(s, 8)  # exactly the minimum
        late = add(
            s, "2026-11-05T07:00:00+00:00", 10000, 2000, avg_hr=140.0
        )  # > 28 d after the rest
        metrics(s, late, is_steady=True, efficiency_factor=1.5)

    d = get(client, "/api/stats/trends", metric="pace", to_date="2026-09-30")
    assert d["n"] == 8 and d["unit"] == "s/km"
    assert d["trend"]["slope_per_day"] == pytest.approx(-2.0) and d["trend"]["span_days"] == 7
    vals = [300 - 2 * i for i in range(8)]
    assert [p["value"] for p in d["points"]] == vals
    assert d["points"][-1]["rolling_median"] == statistics.median(vals)
    assert d["points"][0]["rolling_median"] == 300

    assert get(client, "/api/stats/trends", metric="hr", to_date="2026-09-30")["trend"][
        "slope_per_day"
    ] == pytest.approx(1.0)
    assert get(client, "/api/stats/trends", metric="ef", to_date="2026-09-30")["trend"][
        "slope_per_day"
    ] == pytest.approx(0.01)

    # the 28-day window: a run 2 months later is not averaged with the old ones
    d = get(client, "/api/stats/trends", metric="pace")
    assert d["n"] == 9 and d["points"][-1]["rolling_median"] == 200


def test_theil_sen_is_robust_to_outliers():
    xs = [float(x) for x in range(10)]
    ys = [2.0 * x for x in xs]
    ys[4] = 1000.0
    assert theil_sen(xs, ys) == 2.0
    assert theil_sen([1.0, 1.0], [1.0, 2.0]) is None  # same day: no slope


def test_pace_hr_steady_only(engine, client):
    with Session(engine) as s, s.begin():
        seed_steady(s, 3)
    d = get(client, "/api/stats/pace-hr")
    assert d["n"] == 3
    assert [(p["date"], p["pace_s_per_km"], p["avg_hr"]) for p in d["points"]] == [
        ("2026-09-01", 300, 150),
        ("2026-09-02", 298, 151),
        ("2026-09-03", 296, 152),
    ]


# --- calendar / distribution / zones / top weeks -----------------------------------------
def test_calendar_days(engine, client):
    with Session(engine) as s, s.begin():
        add(s, "2026-03-01T07:00:00+00:00", 5000, 1500)
        add(s, "2026-03-01T17:00:00+00:00", 3000, 900)
        add(s, "2026-03-04T07:00:00+00:00", 8000, 2400)
        add(s, "2025-12-31T07:00:00+00:00", 9000, 2700)
    d = get(client, "/api/stats/calendar", year=2026)
    assert d["year"] == 2026
    assert d["days"] == [
        {"date": "2026-03-01", "run_count": 2, "distance_m": 8000},
        {"date": "2026-03-04", "run_count": 1, "distance_m": 8000},
    ]
    assert get(client, "/api/stats/calendar")["year"] == 2026  # defaults to the current year


def test_distribution_histograms(engine, client):
    dists = [4000, 5000, 7999, 8000, 12000, 25000]
    with Session(engine) as s, s.begin():
        for i, dist in enumerate(dists):
            add(s, f"2026-09-{i + 1:02d}T07:00:00+00:00", dist, round(dist * 0.33))  # ~330 s/km
        add(s, "2026-09-10T07:00:00+00:00", 0, 600)  # no distance: no pace, still a run
    d = get(client, "/api/stats/distribution", field="distance")
    assert (d["unit"], d["n"]) == ("m", 7)
    assert [(b["lo"], b["hi"], b["count"]) for b in d["buckets"]] == [
        (0, 5000, 2),  # 4000 and the 0 m run
        (5000, 8000, 2),
        (8000, 12000, 1),
        (12000, 16000, 1),
        (16000, 21000, 0),
        (21000, None, 1),
    ]
    p = get(client, "/api/stats/distribution", field="pace")
    assert p["n"] == 6 and sum(b["count"] for b in p["buckets"]) == 6
    assert [b["count"] for b in p["buckets"] if b["count"]] == [6]  # all in [330, 360)
    dur = get(client, "/api/stats/distribution", field="duration")
    assert sum(b["count"] for b in dur["buckets"]) == 7


def test_zones_per_bucket(engine, client):
    with Session(engine) as s, s.begin():
        a = add(s, "2026-09-07T07:00:00+00:00")
        b = add(s, "2026-09-09T07:00:00+00:00")
        c = add(s, "2026-09-22T07:00:00+00:00")
        no_hr = add(s, "2026-09-23T07:00:00+00:00")
        metrics(s, a, time_in_zones_s=[60, 120, 180, 240, 300])
        metrics(s, b, time_in_zones_s=[10, 20, 30, 40, 50])
        metrics(s, c, time_in_zones_s=[0, 0, 600, 0, 0])
        metrics(s, no_hr)
    d = get(client, "/api/stats/zones", bucket="week", from_date="2026-09-07")
    assert [(i["start"], i["zones_s"], i["partial"]) for i in d["items"]] == [
        ("2026-09-07", [70, 140, 210, 280, 350], False),
        ("2026-09-14", [0, 0, 0, 0, 0], False),
        ("2026-09-21", [0, 0, 600, 0, 0], False),
        ("2026-09-28", [0, 0, 0, 0, 0], True),
    ]
    assert d["totals_s"] == [70, 140, 810, 280, 350]
    assert get(client, "/api/stats/zones", bucket="month")["items"][0]["start"] == "2026-09-01"


def test_top_weeks_ranking_and_fields(engine, client):
    with Session(engine) as s, s.begin():
        for day, dist, mov, elev in (
            ("2026-09-01", 10000, 3000, 50.0),
            ("2026-09-03", 10000, 3500, 70.0),
            ("2026-09-09", 30000, 9000, 10.0),
            ("2026-09-16", 15000, 4500, 0.0),
        ):
            add(s, f"{day}T07:00:00+00:00", dist, mov, elev_gain_m=elev)
    top = get(client, "/api/stats/top-weeks", limit=2)
    assert [w["start"] for w in top] == ["2026-09-07", "2026-08-31"]
    assert (
        top[1]["run_count"] == 2 and top[1]["distance_m"] == 20000 and top[1]["elev_gain_m"] == 120
    )
    assert top[1]["weighted_pace_s_per_km"] == pytest.approx(6500 / 20)


# --- records ------------------------------------------------------------------------------
def test_records_progression_and_exclusions(engine, client):
    def effort(s, day, secs, dist=5000.0, **kw):
        a = add(s, f"{day}T07:00:00+00:00", **kw)
        s.add(BestEffort(activity_id=a.id, distance_m=dist, elapsed_s=secs, algo_version=1))
        return a

    with Session(engine) as s, s.begin():
        effort(s, "2026-01-01", 1500, workout_type="easy")
        effort(s, "2026-02-01", 1450, workout_type="race")
        effort(s, "2026-03-01", 1480)  # slower: not part of the progression
        effort(s, "2026-04-01", 1400, workout_type="workout")
        effort(s, "2026-05-01", 1300, excluded_from_stats=True)
        effort(s, "2026-06-01", 1100, is_indoor=True, sport_type="treadmill")
        bad = effort(s, "2026-07-01", 1200)
        metrics(s, bad, gps_suspect=True)
        effort(s, "2026-08-01", 1400)  # tie: the earlier one keeps the record
        effort(s, "2026-01-05", 280, dist=1000.0)

    recs = get(client, "/api/records")
    assert [r["label"] for r in recs] == [
        "400m",
        "1K",
        "1mi",
        "5K",
        "10K",
        "Half Marathon",
        "Marathon",
    ]
    by = {r["label"]: r for r in recs}
    five = by["5K"]
    assert [(e["local_date"], e["elapsed_s"]) for e in five["progression"]] == [
        ("2026-01-01", 1500),
        ("2026-02-01", 1450),
        ("2026-04-01", 1400),
    ]
    assert five["best"]["local_date"] == "2026-04-01" and five["best"]["workout_type"] == "workout"
    assert (five["best_race"]["local_date"], five["best_race"]["elapsed_s"]) == ("2026-02-01", 1450)
    assert by["1K"]["best"]["elapsed_s"] == 280 and by["1K"]["best_race"] is None
    assert by["Marathon"]["best"] is None and by["Marathon"]["progression"] == []


# --- scope / validation --------------------------------------------------------------------
def test_types_filter_and_validation(engine, client):
    with Session(engine) as s, s.begin():
        add(s, "2026-09-10T07:00:00+00:00", 10000, 3000)
        add(s, "2026-09-11T07:00:00+00:00", 20000, 7000, sport_type="trail_run")

    def count(*extra):
        r = client.get("/api/stats/summary", params=[("from_date", "2026-09-01"), *extra])
        return r.json()["current"]["run_count"]

    assert count() == 2
    assert count(("types[]", "trail_run")) == 1
    assert count(("types[]", "run"), ("types[]", "trail_run")) == 2
    assert client.get("/api/stats/summary", params=[("types[]", "bike")]).status_code == 422

    for path, params in (
        ("/api/stats/volume", {"bucket": "year"}),
        ("/api/stats/summary", {"from_date": "2026-09-10", "to_date": "2026-09-01"}),
        ("/api/stats/summary", {"from_date": "2026-12-01"}),
        ("/api/stats/distribution", {"field": "speed"}),
        ("/api/stats/distribution", {}),
        ("/api/stats/trends", {"metric": "power"}),
        ("/api/stats/top-weeks", {"limit": 0}),
        ("/api/stats/calendar", {"year": 1800}),
    ):
        assert client.get(path, params=params).status_code == 422, (path, params)


# --- fitness indices ------------------------------------------------------------------
def test_vdot_reference_value():
    assert stats.vdot(5000, 20 * 60) == pytest.approx(49.8, abs=0.2)  # Daniels table: 20:00 5K


def test_fitness_endpoint(engine, client):
    with Session(engine) as s, s.begin():
        for i in range(8):
            a = add(s, f"2026-09-{22 + i:02d}T06:00:00+00:00", 10000, 3000)
            metrics(s, a, trimp=50.0)
        s.add(BestEffort(activity_id=a.id, distance_m=5000.0, elapsed_s=1200, algo_version=1))

    f = get(client, "/api/stats/fitness")
    assert f["vo2max"]["vdot"] == pytest.approx(49.8, abs=0.2)
    assert f["vo2max"]["vdot_source"] == "5K"
    assert {p["label"]: p["seconds"] for p in f["predictions"]}["10K"] == round(1200 * 2**1.06)
    assert f["acwr"] == pytest.approx(6 * 50 / (8 * 50 / 4))  # 6 runs in the last 7 d, 8 in 28 d
    assert f["monotony"] == pytest.approx(6**0.5)  # 6 equal days + 1 rest day
    assert f["ctl"] < f["atl"] and f["tsb"] == pytest.approx(f["ctl"] - f["atl"])


def test_fitness_empty(client):
    f = get(client, "/api/stats/fitness")
    assert f["vo2max"]["vdot"] is None and f["ctl"] is None and f["predictions"] == []
