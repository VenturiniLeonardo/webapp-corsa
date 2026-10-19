import math
from dataclasses import replace
from datetime import date, timedelta

import pytest

from app.metrics.predictions import Anchor, Run, hr_trend, predict

TODAY = date(2026, 10, 19)


def run(age=0, distance=8000, seconds=3200, **kwargs):
    return Run(age + 1, TODAY - timedelta(days=age), distance, seconds, seconds, **kwargs)


def recent():
    return [run(i * 4, hr=150, steady=True) for i in range(12)]


def race(age=14, seconds=3500):
    r = run(age, 10000, seconds, race=True)
    return Anchor(r, 10000, seconds, True)


def test_recent_race_dominates_old_personal_best_and_paces_slow_with_distance():
    old, new = race(200, 3000), race(14, 3500)
    result = predict(recent(), [old, new], TODAY, 10, 5)
    items = result["items"]
    assert items[1]["elapsed_s"] == 3500
    assert items[1]["anchor_activity_id"] == new.run.id
    assert [i["pace_s_per_km"] for i in items] == sorted(i["pace_s_per_km"] for i in items)
    for i in items:
        assert i["pace_low_s_per_km"] < i["pace_s_per_km"] < i["pace_high_s_per_km"]


def test_exact_56_day_window_excludes_old_and_future_training():
    runs = [run(0), run(55), run(56, 50000, 20000), run(-1, 50000, 20000)]
    result = predict(runs, [race()], TODAY)
    assert result["runs_8w"] == 2
    assert result["avg_weekly_distance_m"] == 2000
    assert all(i["elapsed_s"] is None for i in result["items"])


def test_hr_improvement_updates_projection_without_extrapolating_race_hr():
    stable = recent()
    # Same HR and duration, speed increasing by 0.1%/day.
    improving = [
        replace(r, distance_m=r.distance_m * math.exp(-0.001 * (TODAY - r.day).days))
        for r in stable
    ]
    base = predict(stable, [race()], TODAY, 10, 5)
    better = predict(improving, [race()], TODAY, 10, 5)
    assert base["hr_trend_applied"] and better["hr_trend_applied"]
    assert better["hr_efficiency_change_pct"] == pytest.approx(5.8, abs=0.1)
    assert better["items"][1]["elapsed_s"] < base["items"][1]["elapsed_s"]
    missing = predict([replace(r, hr=None) for r in improving], [race()], TODAY)
    assert missing["hr_runs_8w"] == 0 and not missing["hr_trend_applied"]
    assert missing["items"][1]["elapsed_s"] is not None


def test_incomparable_hr_intervals_and_short_span_do_not_produce_trend():
    assert not hr_trend([replace(r, steady=False) for r in recent()])[3]
    compressed = [run(i, hr=150, steady=True) for i in range(14)]
    assert not hr_trend(compressed)[3]
    mixed = [replace(r, hr=120 if i < 6 else 165) for i, r in enumerate(recent())]
    assert hr_trend(mixed)[0] == 0


def test_historical_heat_removed_before_current_heat_is_applied():
    anchor = race()
    hot = replace(anchor, run=replace(anchor.run, temp_c=30, dew_c=22))
    cool = predict(recent(), [hot], TODAY, 10, 5)
    same = predict(recent(), [hot], TODAY, 30, 22)
    assert same["items"][1]["elapsed_s"] == 3500
    assert cool["items"][1]["elapsed_s"] < 3500
    assert same["heat_adjustment_pct"] > 0


def test_marathon_responds_to_preparation_and_carries_more_uncertainty():
    low = predict(recent(), [race()], TODAY, 10, 5)
    prepared = recent() + [run(1, 32000, 14000)] + [run(i + 2, 14000, 6000) for i in range(20)]
    high = predict(prepared, [race()], TODAY, 10, 5)
    assert low["items"][3]["elapsed_s"] > high["items"][3]["elapsed_s"]
    assert low["items"][3]["confidence"] == "low"
    assert any("maratona" in s for s in low["items"][3]["reasons"])
    assert low["items"][3]["pace_high_s_per_km"] - low["items"][3]["pace_low_s_per_km"] > 60


def test_training_fallback_is_identified_and_expired_anchors_are_not_used():
    a = replace(race(), race=False)
    result = predict(recent(), [a], TODAY)
    assert result["items"][0]["confidence"] == "low"
    assert any("allenamento" in s for s in result["items"][0]["reasons"])
    expired = predict(recent(), [race(366)], TODAY)
    assert all(i["elapsed_s"] is None for i in expired["items"])
