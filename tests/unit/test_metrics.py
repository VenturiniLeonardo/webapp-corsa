import pytest

from app.metrics.engine import (
    compute_best_efforts,
    compute_efficiency_factor,
    compute_km_splits,
    compute_steady_run,
    compute_time_in_zones,
    detect_gps_suspect,
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
