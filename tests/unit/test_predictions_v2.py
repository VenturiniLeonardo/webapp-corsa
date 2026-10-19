import json
import math
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.metrics import predictions_v2 as v2
from app.metrics.prediction_data import segment

TODAY = date(2026, 10, 19)


def run(aid=1, age=1, distance=10000, elapsed=3000, **kw):
    return v2.RunV2(aid, TODAY - timedelta(days=age), distance, elapsed, elapsed, **kw)


def preparation_runs(volume, longest, references):
    runs = [r.run for r in references]
    used = sum(r.distance_m for r in runs if (TODAY - r.day).days < 56)
    runs.append(run(100, distance=longest * 1000, elapsed=longest * 400))
    part = (volume * 8000 - used - longest * 1000) / 30
    runs.extend(run(101 + i, i + 2, part, part / 2.5) for i in range(30))
    return runs


@pytest.mark.parametrize("profile", range(5))
def test_specification_golden_examples(profile, monkeypatch):
    examples = json.loads(
        (Path(__file__).parents[1] / "fixtures/predictions_v2/examples.json").read_text(
            encoding="utf-8"
        )
    )
    example = examples[profile]
    if profile == 0:
        refs = [
            v2.Reference(
                run(age=21, elapsed=2700, race=True, temp_c=20, dew_c=12),
                10000,
                2700,
                flat_confirmed=True,
                baseline_volume_km=30,
                baseline_long_km=18,
            )
        ]
    elif profile in (1, 3):
        refs = [
            v2.Reference(
                run(age=300, distance=42195, elapsed=12600, race=True),
                42195,
                12600,
                baseline_volume_km=70,
            )
        ]
        if profile == 3:
            refs.append(
                v2.Reference(
                    run(2, age=10, distance=5000, elapsed=1410),
                    5000,
                    1410,
                    baseline_volume_km=20,
                    baseline_long_km=12,
                )
            )
    else:
        refs = [
            v2.Reference(
                run(age=40, distance=21400, elapsed=5700, race=True),
                21097.5,
                5700,
                grade_factor=1.03 if profile == 4 else None,
                official=True,
                baseline_volume_km=50,
                baseline_long_km=28,
            )
        ]
    monkeypatch.setattr(
        v2,
        "trend",
        lambda *_: {
            "slope_per_day": example["slope"],
            "reliability": example["rho"],
            "support": 12,
            "span_days": 40,
            "pairs": 12,
            "runs": 8,
        },
    )
    runs = preparation_runs(example["V"], example["L"], refs)
    result = v2.predict_v2(runs, refs, TODAY, [(TODAY - timedelta(days=55), TODAY)])
    assert result["avg_weekly_distance_m"] == pytest.approx(example["V"] * 1000)
    for actual, expected in zip(result["items"], example["rows"], strict=True):
        assert actual["elapsed_s"] == pytest.approx(expected["after_s"], abs=1e-7)
        for source, candidate in zip(actual["sources"], expected["v2_candidates"], strict=True):
            assert source["candidate_s"] == pytest.approx(candidate["t"], abs=1e-7)
            assert source["hr_factor"] == pytest.approx(candidate["F"])
            assert source["preparation_factor"] == pytest.approx(candidate["Q"])


def test_median_ties_and_isotonic_envelope():
    assert v2.weighted_quantile([10, 20], [0.5, 0.5]) == 15
    assert v2.weighted_quantile([20, 10], [0.51, 0.49]) == 20
    assert v2.weighted_quantile([10, 10, 20], [0.2, 0.3, 0.5]) == 15
    assert v2.weighted_quantile([10, 20, 30], [1, 1, 8], 0.9) == 30
    with pytest.raises(ValueError):
        v2.weighted_quantile([1], [0])
    out = v2.pava([math.log(x) for x in [300, 370, 365, 420]], [1] * 4)
    assert math.exp(out[1]) == pytest.approx(math.sqrt(370 * 365))
    assert out == sorted(out)
    lo, hi = v2.widen_monotone([290, 360, 350, 400], [310, 390, 380, 450])
    assert lo == [290, 350, 350, 400] and hi == [310, 390, 390, 450]


def test_heat_boundaries_and_duration():
    assert v2.heat_fraction(0, -5) == 0
    assert v2.heat_fraction(50, 50) == 0.2
    assert v2.heat_fraction(None, 10) is None
    assert v2.heat_fraction(10, 20) is None
    assert (
        v2.heat_factor(30, 20, 900) < v2.heat_factor(30, 20, 3600) < v2.heat_factor(30, 20, 10800)
    )
    assert v2.heat_factor(None, None, 3600) == 1


def test_continuous_extrapolation_and_no_penalty_when_projecting_down():
    a = v2.exponent(21097.5, 21097.5 / 1.0999, 20, 12)
    b = v2.exponent(21097.5, 21097.5 / 1.1001, 20, 12)
    assert 0 < b - a < 0.00001
    assert v2.exponent(10000, 42195, 0, 0) == 1.06


def test_stops_not_grade_corrected_and_no_inference_from_ascent():
    ref = v2.Reference(run(elapsed=3100), 10000, 3100, moving_s=3000, grade_factor=1.03)
    assert v2.normalized(ref)[0] == pytest.approx(3000 / 1.03 + 100)
    assert "weather_missing" in v2.normalized(ref)[1]
    assert "grade_missing" in v2.normalized(replace(ref, grade_factor=None))[1]


def test_hr_shrinkage_sensor_and_weather_comparability():
    runs = [
        run(
            i,
            50 - i * 7,
            10000 + i * 100,
            3600,
            hr=150,
            steady=True,
            temp_c=15,
            dew_c=5,
            sensor="chest",
            sensor_id="strap1",
            hr_from_stream=True,
        )
        for i in range(7)
    ]
    signal = v2.trend(runs)
    assert 0 < signal["reliability"] < 1 and signal["slope_per_day"] > 0
    assert v2.trend(runs[:2])["reliability"] < signal["reliability"]
    optical = v2.trend([replace(r, sensor="optical") for r in runs])
    assert optical["reliability"] < signal["reliability"]
    mismatched = [replace(r, sensor_id=str(i)) for i, r in enumerate(runs)]
    assert v2.trend(mismatched)["pairs"] == 0
    assert v2.trend([replace(r, temp_c=None) for r in runs])["reliability"] == 0
    assert v2.trend([replace(r, temp_c=i * 4) for i, r in enumerate(runs)])["pairs"] == 0


def test_decline_not_double_counted_and_unknown_coverage_not_detraining(monkeypatch):
    refs = [
        v2.Reference(
            run(age=300, distance=42195, elapsed=12600, race=True),
            42195,
            12600,
            baseline_volume_km=70,
            baseline_long_km=30,
        )
    ]
    runs = preparation_runs(20, 12, refs)
    monkeypatch.setattr(
        v2,
        "trend",
        lambda *_: {
            "slope_per_day": -0.002,
            "reliability": 0.5,
            "support": 12,
            "span_days": 40,
            "pairs": 12,
            "runs": 8,
        },
    )
    unknown = v2.predict_v2(runs, refs, TODAY)
    assert unknown["items"][-1]["sources"][0]["preparation_factor"] == 1
    known = v2.predict_v2(runs, refs, TODAY, [(TODAY - timedelta(days=55), TODAY)])
    s = known["items"][-1]["sources"][0]
    assert s["candidate_s"] == pytest.approx(
        12600 * max(s["preparation_factor"], 1 / s["hr_factor"])
    )


def test_race_floor_and_asymmetric_monotone_intervals():
    refs = [
        v2.Reference(run(age=300, distance=42195, elapsed=12600, race=True), 42195, 12600),
        v2.Reference(run(2, age=10, distance=5000, elapsed=1410), 5000, 1410),
    ]
    result = v2.predict_v2(preparation_runs(20, 12, refs), refs, TODAY)
    items = result["items"]
    assert all(i["sources"][0]["weight"] >= 0.5 for i in items)
    assert [i["pace_s_per_km"] for i in items] == sorted(i["pace_s_per_km"] for i in items)
    assert [i["pace_low_s_per_km"] for i in items] == sorted(i["pace_low_s_per_km"] for i in items)
    for i in items:
        assert (
            i["pace_high_s_per_km"] - i["pace_s_per_km"]
            > i["pace_s_per_km"] - i["pace_low_s_per_km"]
        )
    standards = [i["elapsed_s"] for i in items]
    v2.apply_weather(items, [{"status": "forecast", "samples": [(30, 22, 1)]} for _ in items])
    assert standards == [i["elapsed_s"] for i in items]
    assert all(i["scenario"]["elapsed_s"] > i["elapsed_s"] for i in items)


def test_segment_coverage_pauses_and_invalid_values():
    ch = {
        "time": list(range(1801)),
        "distance": [i * 3 for i in range(1801)],
        "hr": [160] * 1801,
        "altitude": [100] * 1801,
    }
    result = segment(ch, 0, 1800, 300)
    assert result["hr"] == 160 and result["speed"] == 3
    assert result["grade_factor"] == 1 and result["duration_s"] == 1500
    assert segment({**ch, "time": [None] * 1801}, 0, 1800) == {}
    assert segment({**ch, "distance": [float("nan")] * 1801}, 0, 1800) == {}
    assert segment({**ch, "time": [i * 15 for i in range(1801)]}, 0, 1800) == {}
    assert segment({**ch, "hr": [None] * 1801}, 0, 1800)["hr"] is None
