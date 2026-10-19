from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session
from test_stats_api import add, client, engine, env, metrics  # noqa: F401

from app.domain.models import BestEffort, SourceRecord, Stream
from app.domain.stream_codec import encode_stream
from app.metrics.prediction_data import load_data

NOW = datetime(2026, 10, 19, 12, tzinfo=UTC)


def test_best_effort_needs_intensity_and_uses_fastest_equivalent(engine):  # noqa: F811
    with Session(engine) as db, db.begin():
        a = add(db, (NOW - timedelta(days=1)).isoformat(), dist=15000, mov=6000)
        aid = a.id
        db.add_all(
            [
                BestEffort(
                    activity_id=aid,
                    distance_m=5000,
                    elapsed_s=1250,
                    start_offset_s=0,
                    algo_version=1,
                ),
                BestEffort(
                    activity_id=aid,
                    distance_m=10000,
                    elapsed_s=3300,
                    start_offset_s=1250,
                    algo_version=1,
                ),
            ]
        )
    with Session(engine) as db:
        _, refs, _, _ = load_data(db, NOW.date(), {}, NOW)
        assert not refs
        _, refs, _, _ = load_data(
            db,
            NOW.date(),
            {
                "activities": {
                    str(aid): {"test_verified": True, "test_distance_m": 5000, "test_rpe": 9}
                }
            },
            NOW,
        )
        assert len(refs) == 1 and refs[0].distance_m == 5000
        assert refs[0].intensity_method == "declared_continuous_test_rpe"


def test_official_distance_survives_bad_gps_without_reusing_gap(engine):  # noqa: F811
    with Session(engine) as db, db.begin():
        a = add(
            db, (NOW - timedelta(days=1)).isoformat(), dist=22000, mov=5700, workout_type="race"
        )
        aid = a.id
        metrics(db, a, gps_suspect=True, gap_speed_ms=4)
    with Session(engine) as db:
        assert not load_data(db, NOW.date(), {}, NOW)[1]
        runs, refs, _, _ = load_data(
            db,
            NOW.date(),
            {
                "activities": {
                    str(aid): {
                        "official_verified": True,
                        "official_distance_m": 21097.5,
                        "official_elapsed_s": 5700,
                    }
                }
            },
            NOW,
        )
        assert refs[0].distance_m == 21097.5 and refs[0].official
        assert runs[0].distance_m == 21097.5
        assert refs[0].grade_factor is None and runs[0].gap_speed_ms is None


def test_stream_intensity_chooses_best_equivalent_not_longest(engine):  # noqa: F811
    with Session(engine) as db, db.begin():
        a = add(db, (NOW - timedelta(days=1)).isoformat(), dist=15000, mov=6000)
        source = SourceRecord(
            source="file_fit", external_id="intensity", activity_id=a.id, status="mapped"
        )
        db.add(source)
        db.flush()
        a.stream_source_id = source.id
        # First 5 km fast; next 10 km slower, both above threshold.
        channels = {
            "time": list(range(6001)),
            "distance": [min(i, 1250) * 4 + max(0, i - 1250) * (10000 / 4750) for i in range(6001)],
            "hr": [170] * 6001,
            "altitude": [100] * 6001,
        }
        db.add(
            Stream(
                source_record_id=source.id,
                activity_id=a.id,
                n_points=6001,
                channels="time,distance,hr,altitude",
                data=encode_stream(channels),
                codec_version=1,
            )
        )
        db.add_all(
            [
                BestEffort(
                    activity_id=a.id,
                    distance_m=5000,
                    elapsed_s=1250,
                    start_offset_s=0,
                    algo_version=1,
                ),
                BestEffort(
                    activity_id=a.id,
                    distance_m=10000,
                    elapsed_s=4750,
                    start_offset_s=1250,
                    algo_version=1,
                ),
            ]
        )
    with Session(engine) as db:
        profile = {"profile": {"sensor": "chest", "hr_threshold": 175}}
        _, refs, _, _ = load_data(db, NOW.date(), profile, NOW)
        assert len(refs) == 1 and refs[0].distance_m == 5000
        assert refs[0].intensity_method == "threshold_hr"
        assert not load_data(
            db, NOW.date(), {"profile": {"sensor": "unknown", "hr_threshold": 175}}, NOW
        )[1]
