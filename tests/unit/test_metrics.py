import pytest

from app.metrics.engine import (
    compute_adjusted_ef,
    compute_best_efforts,
    compute_decoupling,
    compute_efficiency_factor,
    compute_gap_distance,
    compute_grades,
    compute_hr_at_pace,
    compute_km_splits,
    compute_split_gap_speeds,
    compute_steady_run,
    compute_time_in_zones,
    detect_gps_suspect,
    heat_slowdown_pct,
    minetti_factor,
)


def constant(speed: float, seconds: int) -> tuple[list[float], list[float]]:
    t = [float(i) for i in range(seconds + 1)]
    return [speed * x for x in t], t


# --- best efforts ---------------------------------------------------------


def test_best_efforts_constant_3ms_10k() -> None:
    d, t = constant(3.0, 3334)  # 10002 m
    got = {e.distance_m: e for e in compute_best_efforts(d, t)}
    assert got[5000.0].elapsed_s == pytest.approx(1666.667, abs=1e-3)
    assert got[10000.0].elapsed_s == pytest.approx(3333.333, abs=1e-3)
    assert got[1609.34].elapsed_s == pytest.approx(1609.34 / 3)
    assert 21097.5 not in got and 42195.0 not in got  # stream shorter than target


def test_best_effort_finds_fast_second_half() -> None:
    d1, t1 = constant(2.5, 2000)
    d2 = [d1[-1] + 4.0 * i for i in range(1, 2001)]
    t2 = [t1[-1] + i for i in range(1, 2001)]
    (e,) = compute_best_efforts(d1 + d2, t1 + t2, [1000.0])
    assert e.elapsed_s == pytest.approx(250.0)
    assert e.start_offset_s >= 2000.0


def test_best_effort_interpolates_end_on_sample() -> None:
    (e,) = compute_best_efforts([0, 300, 600], [0, 100, 150], [400.0])
    # window [200 m, 600 m]: start at t=66.67 (interpolated), end t=150
    assert e.elapsed_s == pytest.approx(83.333, abs=1e-3)
    assert e.start_offset_s == pytest.approx(66.667, abs=1e-3)


def test_best_effort_interpolates_start_on_sample() -> None:
    (e,) = compute_best_efforts([0, 300, 600], [0, 50, 150], [400.0])
    # window [0 m, 400 m]: only found with start on a sample
    assert e.elapsed_s == pytest.approx(83.333, abs=1e-3)
    assert e.start_offset_s == 0.0


def test_best_efforts_empty() -> None:
    assert compute_best_efforts([], []) == []
    assert compute_best_efforts([0.0], [0.0]) == []


# --- km splits ------------------------------------------------------------


def test_splits_5420m_partial_last() -> None:
    d, t = constant(3.0, 1806)  # 5418 m
    d.append(5420.0)
    t.append(5420.0 / 3)
    hr = [150.0] * len(d)
    elev = [0.01 * x for x in d]  # +10 m per km
    splits = compute_km_splits(d, t, hr, elev)

    assert [s.idx for s in splits] == list(range(6))
    assert [round(s.distance_m, 6) for s in splits] == [1000.0] * 5 + [420.0]
    for s in splits[:5]:
        assert s.elapsed_s == pytest.approx(1000 / 3)
        assert s.avg_speed_ms == pytest.approx(3.0)
        assert s.avg_hr == pytest.approx(150.0)
        assert s.elev_gain_m == pytest.approx(10.0)
    assert splits[5].elapsed_s == pytest.approx(140.0)
    assert splits[5].elev_gain_m == pytest.approx(4.2)
    assert splits[2].start_offset_s == pytest.approx(2000 / 3)
    assert sum(s.distance_m for s in splits) == pytest.approx(d[-1] - d[0])
    assert sum(s.elapsed_s for s in splits) == pytest.approx(t[-1] - t[0])


def test_splits_sparse_samples_cross_multiple_boundaries() -> None:
    splits = compute_km_splits([0, 2500], [0, 1000], None, None)
    assert [s.distance_m for s in splits] == [1000, 1000, 500]
    assert [s.elapsed_s for s in splits] == pytest.approx([400, 400, 200])


def test_splits_missing_hr_and_elev() -> None:
    d, t = constant(3.0, 700)
    (s1, _, s3) = compute_km_splits(d, t, None, None)
    assert s1.avg_hr is None and s1.elev_gain_m is None
    assert s3.distance_m == pytest.approx(100.0)


def test_splits_hr_is_time_weighted_and_skips_gaps() -> None:
    d, t = [0.0, 500.0, 1000.0], [0.0, 100.0, 400.0]
    (s,) = compute_km_splits(d, t, [100.0, 100.0, 200.0], None)
    assert s.avg_hr == pytest.approx((100 * 100 + 150 * 300) / 400)
    (s,) = compute_km_splits(d, t, [None, 120.0, 120.0], None)
    assert s.avg_hr == pytest.approx(120.0)


def test_splits_empty() -> None:
    assert compute_km_splits([], [], None, None) == []


# --- zones / EF / steady / gps --------------------------------------------


def test_time_in_zones_with_dt_cap() -> None:
    hr = [100.0, 140.0, 160.0, 170.0, 190.0, 190.0]
    t = [0.0, 1.0, 2.0, 3.0, 4.0, 30.0]  # 26 s pause capped to 10
    assert compute_time_in_zones(hr, t, [130, 150, 165, 180]) == [1, 1, 1, 1, 10]


def test_time_in_zones_boundary_inclusive_and_missing_hr() -> None:
    assert compute_time_in_zones([130.0, 130.0], [0.0, 5.0], [130, 150, 165, 180]) == [
        5,
        0,
        0,
        0,
        0,
    ]
    assert compute_time_in_zones([None, None], [0.0, 5.0], [130, 150, 165, 180]) == [0] * 5
    assert compute_time_in_zones([], [], [130, 150, 165, 180]) == [0] * 5


def test_efficiency_factor() -> None:
    assert compute_efficiency_factor(3.0, 150.0) == pytest.approx(1.2)
    assert compute_efficiency_factor(3.0, None) is None
    assert compute_efficiency_factor(3.0, 0.0) is None


def test_steady_constant_outdoor() -> None:
    _, t = constant(3.0, 1500)
    steady, cv = compute_steady_run([3.0] * len(t), t, False, "easy")
    assert steady is True
    assert cv == pytest.approx(0.0)


@pytest.mark.parametrize(
    ("seconds", "indoor", "wt"),
    [(1500, True, "easy"), (1500, False, "workout"), (1500, False, "race"), (1100, False, None)],
)
def test_not_steady_by_context(seconds: int, indoor: bool, wt: str | None) -> None:
    _, t = constant(3.0, seconds)
    assert compute_steady_run([3.0] * len(t), t, indoor, wt)[0] is False


def test_steady_ignores_stops_but_rejects_variable_pace() -> None:
    _, t = constant(3.0, 1500)
    with_stop = [0.0 if 600 <= x < 720 else 3.0 for x in t]
    assert compute_steady_run(with_stop, t, False, None)[0] is True
    intervals = [2.5 if (x // 60) % 2 else 3.5 for x in t]
    steady, cv = compute_steady_run(intervals, t, False, None)
    assert steady is False
    assert cv is not None and cv > 0.08


def test_steady_empty() -> None:
    assert compute_steady_run([], [], False, None) == (False, None)


def test_gps_suspect() -> None:
    assert detect_gps_suspect([3.0] + [8.0] * 12 + [3.0]) is True  # 11 s above 7 m/s
    assert detect_gps_suspect([3.0] + [8.0] * 11 + [3.0]) is False  # exactly 10 s
    assert detect_gps_suspect([8.0, 8.0], [0.0, 11.0]) is True  # sparse sampling
    assert detect_gps_suspect([]) is False


def test_scale_distance_stream_stretches_to_summary_only_when_longer() -> None:
    from app.metrics.engine import scale_distance_stream

    assert scale_distance_stream([0, 100, 200], 202) == [0, 101, 202]
    assert scale_distance_stream([0, 100, 200], 400) == [0, 100, 200]  # broken stream
    assert scale_distance_stream([0, 10, 20], 15) == [0, 10, 20]
    assert scale_distance_stream([0, 10, 20], None) == [0, 10, 20]


# --- GAP / reference-pace HR / heat (model) -------------------------------------------
def test_gap_flat_equals_distance_and_uphill_is_longer() -> None:
    d = [i * 10.0 for i in range(101)]
    flat = compute_gap_distance(d, compute_grades(d, [5.0] * 101))
    assert flat[-1] == pytest.approx(1000.0)
    up = compute_gap_distance(d, compute_grades(d, [x * 0.05 for x in d]))  # 5% climb
    assert up[-1] - up[0] == pytest.approx(1000.0 * minetti_factor(0.05), rel=1e-6)
    assert minetti_factor(0.05) > 1 > minetti_factor(-0.05)


def test_grades_need_altitude_and_clamp() -> None:
    d = [i * 10.0 for i in range(11)]
    assert compute_grades(d, [None] * 11) == [None] * 11
    assert max(g for g in compute_grades(d, [x * 2 for x in d]) if g is not None) == 0.45


def test_split_gap_speeds() -> None:
    d = [i * 10.0 for i in range(201)]
    t = [float(i * 3) for i in range(201)]
    gap = compute_gap_distance(d, compute_grades(d, [0.0] * 201))
    splits = compute_km_splits(d, t)
    assert compute_split_gap_speeds(d, gap, splits) == pytest.approx([10 / 3, 10 / 3])


def test_hr_at_pace_fits_hr_against_speed() -> None:
    n = 1800  # 30 min at 1 Hz, speed alternating 2.5 / 3.5 m/s every 5 min, HR = 100 + 20 v
    t = [float(i) for i in range(n)]
    v = [2.5 if (i // 300) % 2 else 3.5 for i in range(n)]
    d = [0.0]
    for x in v[:-1]:
        d.append(d[-1] + x)
    hr = [100 + 20 * x for x in v]
    hr[:300] = [90.0] * 300  # warm-up is ignored
    # the 60 s window blends speeds at the switches, HR does not: allow a little slack
    assert compute_hr_at_pace(t, d, hr, None, 330) == pytest.approx(100 + 20 * 1000 / 330, abs=1)
    assert (
        compute_hr_at_pace(t, d, hr, None, 240) is None
    )  # 4:00 is outside the run: no extrapolation
    assert compute_hr_at_pace(t, d, hr, [0.05] * n, 330) is None  # not flat
    assert compute_hr_at_pace(t, d, [150.0] * n, None, 330) is None  # flat HR: slope 0


def test_heat_slowdown_and_adjusted_ef() -> None:
    assert heat_slowdown_pct(10, 0) == 0  # 50 + 32 °F
    assert heat_slowdown_pct(None, 20) == 0
    assert heat_slowdown_pct(30, 22) == pytest.approx(4.5 + (157.6 - 150) * 0.15)  # 86 + 71.6 °F
    assert compute_adjusted_ef(3.0, 150, None, None) == pytest.approx(1.2)
    assert compute_adjusted_ef(3.0, 150, 30, 22) > 1.2


def test_grades_survive_non_monotonic_distance() -> None:
    d = [0.0, 10.0, 20.0, 30.0, 5.0]  # glitch: distance drops at the end
    assert len(compute_grades(d, [0.0] * 5)) == 5


def test_decoupling_halves_by_moving_time() -> None:
    n = 3600  # 60 min at 3 m/s; HR 140 then 154 (+10%) in the second half
    t = [float(i) for i in range(n)]
    d = [3.0 * i for i in range(n)]
    hr = [140.0 if i < 1800 else 154.0 for i in range(n)]
    assert compute_decoupling(t, d, hr) == pytest.approx((1 - 140 / 154) * 100, abs=0.05)
    assert compute_decoupling(t, d, [150.0] * n) == pytest.approx(0.0)
    assert compute_decoupling(t[:2000], d[:2000], hr[:2000]) is None  # < 45 min
    # a 20 min pause (one long gap) counts in neither half
    t2 = [x if i < 1800 else x + 1200 for i, x in enumerate(t)]
    assert compute_decoupling(t2, d, hr) == pytest.approx((1 - 140 / 154) * 100, abs=0.05)
