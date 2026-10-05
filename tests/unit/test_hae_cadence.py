from datetime import datetime, timedelta

from app.ingest.hae import MAX_SPM, _bucketed

S = datetime.fromisoformat("2026-10-04T08:00:20+02:00")  # starts 20s into the minute
E = S + timedelta(minutes=5)


def test_partial_bucket_not_extrapolated_to_nonsense():
    items = [
        {"date": "2026-10-04 08:00:00 +0200", "qty": 90},
        {"date": "2026-10-04 08:01:00 +0200", "qty": 170},
    ]
    out = _bucketed(items, "qty", S, E, True)
    assert all(v <= MAX_SPM for _, v in out)
    # 5s overlap, 90 steps counted -> 1080 spm: dropped
    short = _bucketed([{"date": "2026-10-04 07:59:59 +0200", "qty": 90}], "qty", S, E, True)
    assert short == [] or all(v <= MAX_SPM for _, v in short)
