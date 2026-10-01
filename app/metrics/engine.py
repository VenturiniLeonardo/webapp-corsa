"""Pure metrics engine (PLAN §13.2). Stdlib only, no I/O."""

import math
import statistics
from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass

ALGO_VERSION = 1

BEST_EFFORT_TARGETS = (400.0, 1000.0, 1609.34, 5000.0, 10000.0, 21097.5, 42195.0)
MAX_ZONE_DT_S = 10.0
STEADY_MIN_DURATION_S = 1200
STEADY_WINDOW_S = 60
STEADY_MIN_SPEED_MS = 0.5  # windows slower than this are stops, not pace
GPS_MAX_SPEED_MS = 7.0
GPS_MAX_FAST_S = 10.0


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
