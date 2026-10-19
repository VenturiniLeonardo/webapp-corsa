import json
from dataclasses import asdict
from datetime import date, timedelta

import pytest

from app.metrics.prediction_backtest import evaluate, replay
from app.metrics.predictions_v2 import VERSION, P, Reference, RunV2


def dataset():
    today = date(2026, 10, 19)
    runs = [
        RunV2(i, today - timedelta(days=i + 1), 10000, 3000, 3000, race=i == 0) for i in range(8)
    ]
    ref = Reference(runs[0], 10000, 3000)
    snapshot = {
        "model_version": VERSION,
        "today": today.isoformat(),
        "cutoff_utc": "2026-10-19T12:00:00+00:00",
        "available_at": "2026-10-19T12:01:00+00:00",
        "runs": [asdict(r) for r in runs],
        "references": [asdict(ref)],
        "parameters": asdict(P),
        "inputs": {},
        "weather": {},
        "weather_samples": [{"samples": []} for _ in range(4)],
    }
    return {
        "athlete_id": "a",
        "event_date": "2026-10-20",
        "distance_m": 10000,
        "actual_elapsed_s": 3000,
        "standard_eligible": True,
        "snapshot": json.loads(json.dumps(snapshot, default=str)),
    }


def test_snapshot_replay_and_backtest_metrics():
    record = dataset()
    result = replay(record["snapshot"])
    assert result["items"][1]["elapsed_s"] == pytest.approx(3000)
    report = evaluate([record])
    group = next(iter(report["groups"].values()))
    assert group["n"] == 1 and group["predicted"] == 1
    assert group["mape"] == pytest.approx(0) and group["coverage"] == 1
    assert group["relative_width"] > 0 and not report["calibrated"]


@pytest.mark.parametrize(
    "bad",
    ["same_day", "future_activity", "unverified_standard", "future_weather", "wrong_scenario"],
)
def test_backtest_rejects_leakage_and_incompatible_ground_truth(bad):
    record = dataset()
    if bad == "same_day":
        record["event_date"] = "2026-10-19"
    elif bad == "future_activity":
        record["snapshot"]["runs"][0]["day"] = "2026-10-20"
    elif bad == "unverified_standard":
        record["standard_eligible"] = False
    elif bad == "future_weather":
        record["snapshot"]["weather"]["retrieved_at"] = ["2026-10-20T12:00:00+00:00"]
    else:
        record["product"] = "scenario"
    with pytest.raises(ValueError):
        evaluate([record])
