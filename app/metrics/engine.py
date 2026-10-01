"""Pure metrics engine (PLAN §13.2). Stdlib only, no I/O."""

import math
import statistics
from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from dataclasses import dataclass

ALGO_VERSION = 5

BEST_EFFORT_TARGETS = (400.0, 1000.0, 1609.34, 5000.0, 10000.0, 21097.5, 42195.0)
MAX_ZONE_DT_S = 10.0
STEADY_MIN_DURATION_S = 1200
STEADY_WINDOW_S = 60
STEADY_MIN_SPEED_MS = 0.5  # windows slower than this are stops, not pace
GPS_MAX_SPEED_MS = 7.0
GPS_MAX_FAST_S = 10.0
MAX_DISTANCE_SCALE = 1.05  # larger gaps = partial/broken stream, not GPS drift
GRADE_WINDOW_M = 50.0  # grade over a centred 50 m window: per-sample altitude is too noisy
MAX_GRADE = 0.45  # range Minetti measured
DECOUPLING_MIN_S = 2700  # PLAN M7-04: steady runs > 45 min only
REF_SPEED_WINDOW_S = 60.0  # pace from distance over a centred 60 s window: 1 Hz speed is noisy
REF_MAX_GRADE = 0.02  # flat only
REF_SKIP_START_S = 300.0  # warm-up: HR not settled yet
REF_MIN_SAMPLES_S = 600.0  # at least 10 min of usable samples
REF_SPEED_PCT = (
    0.1,
    0.9,
)  # ref speed must lie inside this speed range of the run: no extrapolation
# Hadley: pace slowdown % vs temperature °F + dew point °F (runner heuristic, `model`)
HEAT_TABLE = (
    (100, 0.0),
    (110, 0.5),
    (120, 1.0),
    (130, 2.0),
    (140, 3.0),
    (150, 4.5),
    (160, 6.0),
    (170, 8.0),
    (180, 10.0),
)


@dataclass(frozen=True)
class SplitKm:
    idx: int
    start_offset_s: float
    distance_m: float
    elapsed_s: float
    avg_speed_ms: float
    avg_hr: float | None
    elev_gain_m: float | None


@dataclass(frozen=True)
class BestEffort:
    distance_m: float
    elapsed_s: float
    start_offset_s: float


def _lerp(a: float, b: float, f: float) -> float:
    return a + (b - a) * f


def compute_km_splits(
    distance_stream: Sequence[float],
    time_stream: Sequence[float],
    hr_stream: Sequence[float | None] | None = None,
    elev_stream: Sequence[float | None] | None = None,
    split_m: float = 1000.0,
) -> list[SplitKm]:
    n = len(distance_stream)
    if n < 2:
        return []
    splits: list[SplitKm] = []
    # accumulators of the open split
    s_t0, s_d0 = float(time_stream[0]), float(distance_stream[0])
    hr_int = hr_dt = gain = 0.0

    def close(t_end: float, d_end: float) -> None:
        nonlocal s_t0, s_d0, hr_int, hr_dt, gain
        el = t_end - s_t0
        splits.append(
            SplitKm(
                idx=len(splits),
                start_offset_s=s_t0 - time_stream[0],
                distance_m=d_end - s_d0,
                elapsed_s=el,
                avg_speed_ms=(d_end - s_d0) / el if el > 0 else 0.0,
                avg_hr=hr_int / hr_dt if hr_dt > 0 else None,
                elev_gain_m=gain if elev_stream is not None else None,
            )
        )
        s_t0, s_d0, hr_int, hr_dt, gain = t_end, d_end, 0.0, 0.0, 0.0

    def add(
        t0: float, t1: float, h0: float | None, h1: float | None, e0: float | None, e1: float | None
    ) -> None:
        nonlocal hr_int, hr_dt, gain
        if h0 is not None and h1 is not None and t1 > t0:
            hr_int += (h0 + h1) / 2 * (t1 - t0)
            hr_dt += t1 - t0
        if e0 is not None and e1 is not None and e1 > e0:
            gain += e1 - e0

    next_b = (math.floor(s_d0 / split_m) + 1) * split_m
    for i in range(1, n):
        d0, d1 = float(distance_stream[i - 1]), float(distance_stream[i])
        t0, t1 = float(time_stream[i - 1]), float(time_stream[i])
        h0 = hr_stream[i - 1] if hr_stream else None
        h1 = hr_stream[i] if hr_stream else None
        e0 = elev_stream[i - 1] if elev_stream else None
        e1 = elev_stream[i] if elev_stream else None
        while d1 >= next_b > d0:  # segment crosses one or more boundaries
            f = (next_b - d0) / (d1 - d0)
            tb = _lerp(t0, t1, f)
            hb = _lerp(h0, h1, f) if h0 is not None and h1 is not None else None
            eb = _lerp(e0, e1, f) if e0 is not None and e1 is not None else None
            add(t0, tb, h0, hb, e0, eb)
            close(tb, next_b)
            d0, t0, h0, e0 = next_b, tb, hb, eb
            next_b += split_m
        add(t0, t1, h0, h1, e0, e1)
    d_last = float(distance_stream[-1])
    if d_last > s_d0:
        close(float(time_stream[-1]), d_last)
    return splits


def _best_ending_at_samples(
    d: Sequence[float], t: Sequence[float], target: float
) -> tuple[float, float, float]:
    """Min time for `target` m with the window END on a sample; start interpolated. O(n).

    Returns (elapsed, start_t, end_t).
    """
    best, i = (math.inf, 0.0, 0.0), 0
    for j in range(1, len(d)):
        start_d = d[j] - target
        if start_d < d[0]:
            continue
        while d[i + 1] < start_d:  # two pointers: keep d[i] <= start_d <= d[i+1]
            i += 1
        seg = d[i + 1] - d[i]
        ts = _lerp(t[i], t[i + 1], (start_d - d[i]) / seg) if seg > 0 else t[i]
        if t[j] - ts < best[0]:
            best = (t[j] - ts, ts, t[j])
    return best


def scale_distance_stream(d: Sequence[float], total_m: float | None) -> list[float]:
    """Stretch the stream so it ends at the summary distance when the summary is longer
    (provider-corrected distance or footpod total vs raw GPS track). Capped at
    MAX_DISTANCE_SCALE: beyond that the stream is incomplete and is left untouched."""
    out = [float(x) for x in d]
    if total_m and out and out[-1] > 0 and out[-1] < total_m <= out[-1] * MAX_DISTANCE_SCALE:
        k = total_m / out[-1]
        out = [x * k for x in out]
    return out


def compute_best_efforts(
    distance_stream: Sequence[float],
    time_stream: Sequence[float],
    targets: Sequence[float] = BEST_EFFORT_TARGETS,
) -> list[BestEffort]:
    """The optimum of a piecewise-linear window has one end on a sample: check both.

    Start-on-sample is solved as end-on-sample on the reversed, negated stream.
    """
    d = [float(x) for x in distance_stream]
    t = [float(x) for x in time_stream]
    if len(d) < 2:
        return []
    rd, rt = [-x for x in reversed(d)], [-x for x in reversed(t)]
    out: list[BestEffort] = []
    for target in targets:
        if d[-1] - d[0] < target:
            continue
        fwd_el, fwd_start, _ = _best_ending_at_samples(d, t, target)
        rev_el, _, rev_end = _best_ending_at_samples(rd, rt, target)
        el, start = (fwd_el, fwd_start) if fwd_el <= rev_el else (rev_el, -rev_end)
        out.append(BestEffort(target, el, start - t[0]))
    return out


def compute_time_in_zones(
    hr_stream: Sequence[float | None], time_stream: Sequence[float], zones: Sequence[float]
) -> list[int]:
    """`zones` = ascending upper bounds of Z1..Z4 (bpm, inclusive); above the last is Z5.

    Each sample's HR is held until the next sample, Δt capped at 10 s (pauses).
    """
    secs = [0.0] * 5
    for i in range(len(hr_stream) - 1):
        hr = hr_stream[i]
        if hr is None:
            continue
        dt = min(float(time_stream[i + 1] - time_stream[i]), MAX_ZONE_DT_S)
        secs[min(bisect_left(zones, hr), 4)] += max(dt, 0.0)
    return [round(s) for s in secs]


def compute_efficiency_factor(avg_speed_ms: float, avg_hr: float | None) -> float | None:
    return avg_speed_ms * 60 / avg_hr if avg_hr else None


def compute_steady_run(
    speed_stream: Sequence[float | None],
    time_stream: Sequence[float],
    is_indoor: bool,
    workout_type: str | None,
    threshold: float = 0.08,
) -> tuple[bool, float | None]:
    """(is_steady, pace_cv). CV of mean pace across 60 s windows; stop windows ignored."""
    windows: dict[int, list[float]] = {}
    for v, t in zip(speed_stream, time_stream, strict=True):
        if v is not None:
            windows.setdefault(int((t - time_stream[0]) // STEADY_WINDOW_S), []).append(v)
    paces = [1 / m for w in windows.values() if (m := statistics.fmean(w)) >= STEADY_MIN_SPEED_MS]
    if len(paces) < 2:
        return False, None
    cv = statistics.pstdev(paces) / statistics.fmean(paces)
    duration = time_stream[-1] - time_stream[0]
    steady = (
        not is_indoor
        and duration >= STEADY_MIN_DURATION_S
        and workout_type not in ("workout", "race")
        and cv < threshold
    )
    return steady, cv


def detect_gps_suspect(
    speed_stream: Sequence[float | None], time_stream: Sequence[float] | None = None
) -> bool:
    """True if speed > 7 m/s for more than 10 consecutive seconds (1 Hz if no time stream)."""
    t = time_stream if time_stream is not None else range(len(speed_stream))
    run_start: float | None = None
    for v, ti in zip(speed_stream, t, strict=True):
        if v is not None and v > GPS_MAX_SPEED_MS:
            run_start = ti if run_start is None else run_start
            if ti - run_start > GPS_MAX_FAST_S:
                return True
        else:
            run_start = None
    return False


def compute_grades(
    distance_stream: Sequence[float], alt_stream: Sequence[float | None]
) -> list[float | None]:
    """Per-sample grade (rise/run) over a centred GRADE_WINDOW_M window, clamped to +-MAX_GRADE."""
    d, half = distance_stream, GRADE_WINDOW_M / 2
    out: list[float | None] = []
    for i in range(len(d)):
        # min/max with i: a non-monotonic stream (GPS distance glitch) must not index outside it
        lo = min(bisect_left(d, d[i] - half), i)
        hi = max(bisect_right(d, d[i] + half) - 1, i)
        a0, a1 = alt_stream[lo], alt_stream[hi]
        run = d[hi] - d[lo]
        if a0 is None or a1 is None or run < GRADE_WINDOW_M / 4:
            out.append(None)
        else:
            out.append(max(-MAX_GRADE, min(MAX_GRADE, (a1 - a0) / run)))
    return out


def minetti_factor(grade: float) -> float:
    """Energy cost of running at `grade` relative to flat (Minetti 2002, J/kg/m polynomial)."""
    g = grade
    return (155.4 * g**5 - 30.4 * g**4 - 43.3 * g**3 + 46.3 * g**2 + 19.5 * g + 3.6) / 3.6


def compute_gap_distance(
    distance_stream: Sequence[float], grades: Sequence[float | None]
) -> list[float]:
    """Cumulative flat-equivalent distance: each segment weighted by its Minetti cost."""
    out = [float(distance_stream[0])] if distance_stream else []
    for i in range(1, len(distance_stream)):
        g = grades[i]
        f = minetti_factor(g) if g is not None else 1.0
        out.append(out[-1] + (distance_stream[i] - distance_stream[i - 1]) * f)
    return out


def interp(xs: Sequence[float], ys: Sequence[float], x: float) -> float:
    """Linear interpolation on non-decreasing `xs`, clamped at the ends."""
    j = bisect_left(xs, x)
    if j <= 0:
        return ys[0]
    if j >= len(xs):
        return ys[-1]
    seg = xs[j] - xs[j - 1]
    return _lerp(ys[j - 1], ys[j], (x - xs[j - 1]) / seg) if seg > 0 else ys[j]


def compute_split_gap_speeds(
    distance_stream: Sequence[float], gap_distance: Sequence[float], splits: Sequence[SplitKm]
) -> list[float | None]:
    out: list[float | None] = []
    start = float(distance_stream[0])
    for sp in splits:
        eq = interp(distance_stream, gap_distance, start + sp.distance_m) - interp(
            distance_stream, gap_distance, start
        )
        out.append(eq / sp.elapsed_s if sp.elapsed_s > 0 else None)
        start += sp.distance_m
    return out


def compute_hr_at_pace(
    time_stream: Sequence[float],
    distance_stream: Sequence[float],
    hr_stream: Sequence[float | None],
    grades: Sequence[float | None] | None,
    ref_pace_s: float,
) -> float | None:
    """HR at `ref_pace_s` from a per-run least-squares line HR ~ speed (`model`).

    Samples: after REF_SKIP_START_S, flat (|grade| <= REF_MAX_GRADE; unknown grade = flat),
    speed = distance over a centred REF_SPEED_WINDOW_S window, time-weighted by Δt (capped).
    None with < REF_MIN_SAMPLES_S of samples, ref speed outside the run's 10th-90th speed
    percentile, or a non-positive slope (HR not following pace: bad sensor or all-out effort).
    """
    t, d, half = time_stream, distance_stream, REF_SPEED_WINDOW_S / 2
    vs: list[float] = []
    hs: list[float] = []
    ws: list[float] = []
    for i in range(len(t) - 1):
        h, g = hr_stream[i], grades[i] if grades else None
        dt = min(float(t[i + 1] - t[i]), MAX_ZONE_DT_S)
        if h is None or dt <= 0 or t[i] - t[0] < REF_SKIP_START_S:
            continue
        if g is not None and abs(g) > REF_MAX_GRADE:
            continue
        lo, hi = min(bisect_left(t, t[i] - half), i), max(bisect_right(t, t[i] + half) - 1, i)
        if t[hi] > t[lo] and (v := (d[hi] - d[lo]) / (t[hi] - t[lo])) >= STEADY_MIN_SPEED_MS:
            vs.append(v)
            hs.append(float(h))
            ws.append(dt)
    if sum(ws) < REF_MIN_SAMPLES_S:
        return None
    srt = sorted(vs)
    v_ref = 1000 / ref_pace_s
    if not srt[int(len(srt) * REF_SPEED_PCT[0])] <= v_ref <= srt[int(len(srt) * REF_SPEED_PCT[1])]:
        return None
    w = sum(ws)
    mv, mh = (
        sum(v * x for v, x in zip(vs, ws, strict=True)) / w,
        sum(h * x for h, x in zip(hs, ws, strict=True)) / w,
    )
    var = sum(x * (v - mv) ** 2 for v, x in zip(vs, ws, strict=True))
    if var <= 0:
        return None
    slope = sum(x * (v - mv) * (h - mh) for v, h, x in zip(vs, hs, ws, strict=True)) / var
    return mh + slope * (v_ref - mv) if slope > 0 else None


def heat_slowdown_pct(temp_c: float | None, dew_point_c: float | None) -> float:
    """Expected pace slowdown % from heat+humidity (Hadley T+DP table, linear between rows)."""
    if temp_c is None or dew_point_c is None:
        return 0.0
    x = (temp_c * 9 / 5 + 32) + (dew_point_c * 9 / 5 + 32)
    xs, ys = [r[0] for r in HEAT_TABLE], [r[1] for r in HEAT_TABLE]
    return interp(xs, ys, x)


def compute_adjusted_ef(
    gap_speed_ms: float | None,
    avg_hr: float | None,
    temp_c: float | None,
    dew_point_c: float | None,
) -> float | None:
    """EF on grade-adjusted speed, scaled up by the heat slowdown (`model`)."""
    ef = compute_efficiency_factor(gap_speed_ms, avg_hr) if gap_speed_ms else None
    return ef * (1 + heat_slowdown_pct(temp_c, dew_point_c) / 100) if ef else None


def compute_decoupling(
    time_stream: Sequence[float],
    distance_stream: Sequence[float],
    hr_stream: Sequence[float | None],
) -> float | None:
    """Aerobic decoupling Pa:HR, % (Friel): (EF first half - EF second half) / EF first half.

    Halves split at the midpoint of moving time; EF = distance / moving time / time-weighted HR.
    Gaps > MAX_ZONE_DT_S are pauses and count in neither half. Positive = HR drifted up.
    Pass the GAP distance stream to neutralise a course that climbs in one half.
    """
    segs = [
        (t0, t1 - t0, d1 - d0, h)
        for t0, t1, d0, d1, h in zip(
            time_stream,
            time_stream[1:],
            distance_stream,
            distance_stream[1:],
            hr_stream,
            strict=False,
        )
        if 0 < t1 - t0 <= MAX_ZONE_DT_S and h is not None
    ]
    moving = sum(dt for _, dt, _, _ in segs)
    if moving < DECOUPLING_MIN_S:
        return None
    halves = [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]  # moving s, metres, HR*s
    acc = 0.0
    for _, dt, dd, h in segs:
        half = halves[acc >= moving / 2]
        half[0], half[1], half[2] = half[0] + dt, half[1] + dd, half[2] + h * dt
        acc += dt
    (t1, d1, h1), (t2, d2, h2) = halves
    if not (d1 > 0 and d2 > 0 and h1 > 0 and h2 > 0):
        return None
    ef1, ef2 = (d1 / t1) / (h1 / t1), (d2 / t2) / (h2 / t2)
    return (ef1 - ef2) / ef1 * 100
