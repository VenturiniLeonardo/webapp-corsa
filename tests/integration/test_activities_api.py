import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_session, make_engine
from app.domain.models import (
    Activity,
    ActivityMetrics,
    Base,
    BestEffort,
    Lap,
    Setting,
    SourceRecord,
    Stream,
)
from app.domain.stream_codec import encode_stream
from app.main import app

CSRF = {"X-Corsa": "1"}


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
    with Session(e) as s, s.begin():
        s.add(Setting(key="hr_zones", value=[130, 145, 160, 175]))
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


def add(s, name, day="2026-09-01", dist=10000.0, mov=3000, **kw) -> Activity:
    a = Activity(
        **{
            "sport_type": "run",
            "name": name,
            "start_time_utc": f"{day}T07:00:00+00:00",
            "local_date": day,
            "distance_m": dist,
            "moving_s": mov,
            "elapsed_s": mov,
            "avg_hr": 150.0,
            "workout_type": "easy",
            **kw,
        }
    )
    s.add(a)
    s.flush()
    return a


def tag_rows(client, aid, names):
    assert (
        client.patch(f"/api/activities/{aid}", json={"tags": names}, headers=CSRF).status_code
        == 200
    )


def names(r):
    assert r.status_code == 200, r.text
    return {i["name"] for i in r.json()["items"]}


# --- M3-01: list / filters / aggregate -------------------------------------------
@pytest.fixture
def seeded(engine, client):
    with Session(engine) as s, s.begin():
        a1 = add(s, "a1", "2026-08-01", 5000, 1500, avg_hr=140.0)  # 300 s/km
        a2 = add(s, "a2", "2026-08-15", 10000, 3300, workout_type="long")  # 330 s/km
        a3 = add(s, "a3", "2026-09-01", 21000, 7560, avg_hr=160.0, workout_type="race",
                 sport_type="trail_run", notes="Hills at 100% effort")  # 360 s/km  # fmt: skip
        add(s, "a4", "2026-09-10", 8000, 2400, avg_hr=None, sport_type="treadmill")  # 300 s/km
        add(s, "x", "2026-09-05", 12000, 3600, excluded_from_stats=True)
        add(s, "d", "2026-08-01", 5000, 1500, duplicate_of_id=a1.id, excluded_from_stats=True)
        s.add_all(
            SourceRecord(source="strava", external_id=str(a.id), activity_id=a.id, status="mapped")
            for a in (a1, a2)
        )
        ids = {a.name: a.id for a in (a1, a2, a3)}
    return ids


ALL = {"a1", "a2", "a3", "a4"}


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ([], ALL),
        ([("include_excluded", "true")], ALL | {"x", "d"}),
        ([("dist_min", 8000), ("dist_max", 10000)], {"a2", "a4"}),
        ([("from_date", "2026-08-15"), ("to_date", "2026-09-01")], {"a2", "a3"}),
        ([("dur_min", 3000), ("dur_max", 3600)], {"a2"}),
        ([("pace_max", 300)], {"a1", "a4"}),
        ([("pace_min", 350)], {"a3"}),
        ([("hr_min", 145)], {"a2", "a3"}),  # null HR never matches a range
        ([("type[]", "trail_run"), ("type[]", "treadmill")], {"a3", "a4"}),
        ([("workout_type[]", "race")], {"a3"}),
        ([("source[]", "strava")], {"a1", "a2"}),
        ([("q", "hills")], {"a3"}),
        ([("q", "%")], {"a3"}),  # LIKE wildcards are escaped
        ([("tag[]", "base")], {"a1", "a2"}),
        ([("tag[]", "BASE")], {"a1", "a2"}),  # tags are case-insensitive
        ([("tag[]", "base"), ("tag[]", "prep")], {"a1", "a2", "a3"}),  # any-of
        ([("tag[]", "base"), ("dist_min", 6000)], {"a2"}),  # filters combine with AND
        ([("tag[]", "nope")], set()),
    ],
)
def test_filter_matrix(client, seeded, params, expected):
    tag_rows(client, seeded["a1"], ["base"])
    tag_rows(client, seeded["a2"], ["base", "prep"])
    tag_rows(client, seeded["a3"], ["prep"])
    assert names(client.get("/api/activities", params=params)) == expected


def test_sort_and_paging(client, seeded):
    r = client.get("/api/activities", params={"sort": "distance", "order": "asc", "page_size": 2})
    assert [i["name"] for i in r.json()["items"]] == ["a1", "a4"]
    assert r.json()["total"] == 4
    r = client.get(
        "/api/activities", params={"sort": "pace", "order": "desc", "page": 2, "page_size": 2}
    )
    assert [i["name"] for i in r.json()["items"]] == ["a4", "a1"]  # 300 s/km tie -> id desc
    assert client.get("/api/activities", params={"sort": "id; drop"}).status_code == 422
    assert client.get("/api/activities", params={"type[]": "cycling"}).status_code == 422
    assert client.get("/api/activities", params={"page_size": 500}).status_code == 422


def test_weighted_pace_aggregate(engine, client):
    with Session(engine) as s, s.begin():
        add(s, "fast", "2026-09-01", 10000, 3000)  # 300 s/km
        add(s, "slow", "2026-09-02", 5000, 2000)  # 400 s/km
        add(s, "skip", "2026-09-03", 50000, 50000, excluded_from_stats=True)
    agg = client.get("/api/activities", params={"page_size": 1}).json()[
        "aggregate"
    ]  # covers all pages
    assert agg["count"] == 2 and agg["distance_m"] == 15000 and agg["moving_s"] == 5000
    assert agg["weighted_pace_s_per_km"] == pytest.approx(5000 / 15)  # not the 350 mean of paces
    empty = client.get("/api/activities", params={"q": "zzz"}).json()
    assert empty["aggregate"] == {
        "count": 0, "distance_m": 0, "moving_s": 0, "weighted_pace_s_per_km": None
    }  # fmt: skip


# --- M3-02: detail / streams / similar ------------------------------------------
def test_detail_pr_flags_laps_sources_duplicates(engine, client):
    with Session(engine) as s, s.begin():
        old = add(s, "old", "2026-08-01", excluded_from_stats=True)  # faster but not counted
        e1 = add(s, "e1", "2026-08-10")
        e2 = add(s, "e2", "2026-08-20")
        e3 = add(s, "e3", "2026-08-30")
        dup = add(s, "dup", "2026-08-10", duplicate_of_id=e1.id, excluded_from_stats=True)
        for a, secs in ((old, 1000), (e1, 1500), (e2, 1400), (e3, 1450)):
            s.add(BestEffort(activity_id=a.id, distance_m=5000, elapsed_s=secs, algo_version=1))
        s.add(BestEffort(activity_id=e1.id, distance_m=1000, elapsed_s=280, algo_version=1))
        s.add(Lap(activity_id=e1.id, kind="device_lap", idx=0, distance_m=10000))
        s.add_all(Lap(activity_id=e1.id, kind="split_km", idx=i, distance_m=1000) for i in range(2))
        rec = SourceRecord(source="strava", external_id="1", activity_id=e1.id, status="mapped")
        s.add(rec)
        s.flush()
        e1.primary_source_id = rec.id
        s.add(ActivityMetrics(activity_id=e1.id, algo_version=1, efficiency_factor=1.2))
        ids = {"e1": e1.id, "e2": e2.id, "e3": e3.id, "dup": dup.id}

    def detail(k):
        r = client.get(f"/api/activities/{ids[k]}")
        assert r.status_code == 200
        return r.json()

    d = detail("e1")
    assert {b["distance_m"]: b["is_pr"] for b in d["best_efforts"]} == {1000: True, 5000: True}
    assert [x["is_pr"] for x in detail("e2")["best_efforts"]] == [True]
    assert [x["is_pr"] for x in detail("e3")["best_efforts"]] == [False]
    assert [x["idx"] for x in d["splits"]] == [0, 1] and len(d["laps"]) == 1
    assert d["metrics"]["efficiency_factor"] == 1.2 and detail("e2")["metrics"] is None
    assert [(x["source"], x["is_primary"]) for x in d["sources"]] == [("strava", True)]
    assert [x["id"] for x in d["duplicate_candidates"]] == [ids["dup"]]
    assert [x["id"] for x in detail("dup")["duplicate_candidates"]] == [ids["e1"]]
    assert d["activity"]["name"] == "e1" and d["tags"] == []


def test_streams_channel_selection_gzip(engine, client):
    ch = {
        "time": [0, 1, 2],
        "distance": [0.0, 3.0, 6.0],
        "hr": [120, 125, 130],
        "speed": [3.0, 3.0, 3.0],
        "lat": [45.0, 45.1, 45.2],
        "lng": [9.0, 9.1, 9.2],
        "moving": [True, True, True],
    }
    with Session(engine) as s, s.begin():
        a = add(s, "with")
        bare = add(s, "bare")
        rec = SourceRecord(source="strava", external_id="1", activity_id=a.id, status="mapped")
        s.add(rec)
        s.flush()
        a.primary_source_id = a.stream_source_id = rec.id
        s.add(Stream(source_record_id=rec.id, activity_id=a.id, n_points=3,
                     channels=",".join(ch), data=encode_stream(ch), codec_version=1))  # fmt: skip
        aid, bid = a.id, bare.id

    r = client.get(f"/api/activities/{aid}/streams", params={"channels": "time,hr"})
    assert r.headers["content-encoding"] == "gzip"
    assert r.json() == {"channels": {"time": ch["time"], "hr": ch["hr"]}}
    all_ = client.get(f"/api/activities/{aid}/streams").json()["channels"]
    assert set(all_) == set(ch) - {"moving"}
    assert (
        client.get(f"/api/activities/{aid}/streams", params={"channels": "hr,bogus"}).status_code
        == 422
    )
    assert client.get(f"/api/activities/{bid}/streams").json() == {"channels": {}}


def test_similar_filters_orders_and_deltas(engine, client):
    with Session(engine) as s, s.begin():
        base = add(s, "base", "2026-09-20", 10000, 3000, avg_hr=150.0)  # 300 s/km
        s.add(ActivityMetrics(activity_id=base.id, algo_version=1, efficiency_factor=1.2))
        add(s, "low", "2026-09-15", 8400, 2520)  # < -15%
        add(s, "high", "2026-09-16", 11600, 3480)  # > +15%
        add(s, "tread", "2026-09-17", 10000, 3000, sport_type="treadmill")
        add(s, "excl", "2026-09-18", 10000, 3000, excluded_from_stats=True)
        for day in range(1, 8):  # 7 candidates, only the latest 5 are returned
            c = add(s, f"c{day}", f"2026-09-0{day}", 9500, 2945 if day == 7 else 2850,
                    avg_hr=145.0 if day == 7 else 150.0)  # fmt: skip
            if day == 7:
                s.add(ActivityMetrics(activity_id=c.id, algo_version=1, efficiency_factor=1.0))
        bid = base.id

    r = client.get(f"/api/activities/{bid}/similar")
    assert r.status_code == 200
    out = r.json()
    assert [x["name"] for x in out] == ["c7", "c6", "c5", "c4", "c3"]
    assert out[0]["pace_s_per_km"] == pytest.approx(310)
    assert out[0]["pace_delta_s_per_km"] == pytest.approx(-10)  # this run is 10 s/km faster
    assert out[0]["hr_delta_bpm"] == 5
    assert out[0]["ef_delta"] == pytest.approx(0.2)
    assert out[1]["pace_delta_s_per_km"] == pytest.approx(0) and out[1]["ef_delta"] is None


@pytest.mark.parametrize("path", ["", "/streams", "/similar"])
def test_missing_activity_404(client, path):
    assert client.get(f"/api/activities/999{path}").status_code == 404
    assert client.patch("/api/activities/999", json={}, headers=CSRF).status_code == 404


# --- M3-03: PATCH / tags / sync does not clobber --------------------------------
def test_patch_updates_local_fields_only(engine, client):
    with Session(engine) as s, s.begin():
        a = add(s, "orig", dist=10000, mov=3000)
        aid = a.id

    r = client.patch(
        f"/api/activities/{aid}",
        json={
            "notes": "felt good",
            "workout_type": "race",
            "excluded_from_stats": True,
            "tags": ["Tempo", "tempo", " Hills "],
        },
        headers=CSRF,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["notes"], body["workout_type"], body["excluded_from_stats"]) == (
        "felt good",
        "race",
        True,
    )
    assert body["tags"] == ["Hills", "Tempo"]  # deduped case-insensitively, trimmed
    assert (body["name"], body["distance_m"], body["moving_s"]) == ("orig", 10000, 3000)

    # partial patch: only `tags` changes; existing tag row is reused, not duplicated
    r = client.patch(f"/api/activities/{aid}", json={"tags": ["TEMPO"]}, headers=CSRF).json()
    assert r["tags"] == ["Tempo"] and r["notes"] == "felt good" and r["workout_type"] == "race"
    assert client.get("/api/tags").json() == [{"name": "Tempo", "count": 1}]

    r = client.patch(
        f"/api/activities/{aid}", json={"notes": None, "workout_type": None}, headers=CSRF
    )
    assert r.json()["notes"] is None and r.json()["workout_type"] is None
    assert client.get(f"/api/activities/{aid}").json()["activity"]["excluded_from_stats"] is True

    for bad in ({"distance_m": 1}, {"name": ""}, {"workout_type": "bogus"},
                {"excluded_from_stats": None}, {"tags": [""]}):  # fmt: skip
        assert client.patch(f"/api/activities/{aid}", json=bad, headers=CSRF).status_code == 422
    assert (
        client.patch(f"/api/activities/{aid}", json={"notes": "x"}).status_code == 403
    )  # no X-Corsa


def test_rename_and_delete(engine, client):
    with Session(engine) as s, s.begin():
        aid = add(s, "orig", dist=10000, mov=3000).id
    r = client.patch(f"/api/activities/{aid}", json={"name": " Nuovo "}, headers=CSRF)
    assert r.json()["name"] == "Nuovo"
    assert (
        client.delete(
            f"/api/activities/{aid}", headers=CSRF | {"Content-Type": "application/json"}
        ).status_code
        == 204
    )
    assert client.get(f"/api/activities/{aid}").status_code == 404
