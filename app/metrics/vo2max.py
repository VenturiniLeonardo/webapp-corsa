"""Submaximal HR-reserve estimate; model, not a measured VO2max.

Use matched speed/HR intervals after warm-up, retaining regular ten-minute
blocks rather than requiring an entire long run to have a constant pace.
"""

import math
import statistics
from collections import defaultdict
from itertools import pairwise
from typing import Any

WARMUP_S = 300
BLOCK_S = 600
MIN_COVERAGE = 0.8
HALF_LIFE_DAYS = 14  # smoothing choice, not a physiological time constant


def hr_estimate(speed_ms: float, avg_hr: float, hr_max: float, hr_rest: float) -> float | None:
    """ACSM level-running demand + Swain %HRR ~= %VO2 reserve (PMID 9502363)."""
    if not all(math.isfinite(x) for x in (speed_ms, avg_hr, hr_max, hr_rest)):
        return None
    if hr_max <= hr_rest or not 1 <= speed_ms <= 7 or not 40 <= avg_hr <= 220:
        return None
    fraction = (avg_hr - hr_rest) / (hr_max - hr_rest)
    if not 0.3 < fraction <= 1:
        return None
    return 0.2 * speed_ms * 60 / fraction + 3.5


def stream_estimate(
    channels: dict[str, list[Any]], hr_max: float, hr_rest: float, cv_threshold: float = 0.08
) -> float | None:
    """One observation per run: mean of covered, steady ten-minute blocks.

    Intervals longer than 10 s, stopped/missing samples and implausible speeds
    are not filled. Each block needs 80% paired speed/HR coverage, eight covered
    minutes and a pace CV below the configured threshold. Partial tail blocks
    are omitted. No whole-run average is substituted for a rejected stream.
    """
    ts, ds, hrs = (channels.get(k, []) for k in ("time", "distance", "hr"))
    if len(ts) < 2 or len(ds) != len(ts) or len(hrs) != len(ts):
        return None
    if any(not isinstance(t, (int, float)) or not math.isfinite(t) for t in ts):
        return None
    if any(b < a for a, b in pairwise(ts)):
        return None
    start = ts[0] + WARMUP_S
    blocks = int((ts[-1] - start) // BLOCK_S)
    # block -> minute -> (paired seconds, distance, HR-seconds)
    minutes: dict[int, dict[int, list[float]]] = defaultdict(
        lambda: defaultdict(lambda: [0.0, 0.0, 0.0])
    )
    for i in range(len(ts) - 1):
        dt = ts[i + 1] - ts[i]
        d0, d1, hr = ds[i], ds[i + 1], hrs[i]
        if not 0 < dt <= 10 or not all(
            isinstance(x, (int, float)) and math.isfinite(x) for x in (d0, d1, hr)
        ):
            continue
        speed = (d1 - d0) / dt
        if hr_estimate(speed, hr, hr_max, hr_rest) is None:
            continue
        left, right = max(start, ts[i]), min(ts[i + 1], start + blocks * BLOCK_S)
        while left < right:
            slot = int((left - start) // 60)
            stop = min(right, start + (slot + 1) * 60)
            seconds = stop - left
            m = minutes[slot // 10][slot % 10]
            m[0] += seconds
            m[1] += speed * seconds
            m[2] += hr * seconds
            left = stop

    estimates = []
    for block in minutes.values():
        covered = [m for m in block.values() if m[0] >= MIN_COVERAGE * 60]
        duration = sum(m[0] for m in covered)
        if len(covered) < 8 or duration < MIN_COVERAGE * BLOCK_S:
            continue
        paces = [m[0] / m[1] for m in covered]
        if statistics.pstdev(paces) / statistics.fmean(paces) >= cv_threshold:
            continue
        value = hr_estimate(
            sum(m[1] for m in covered) / duration,
            sum(m[2] for m in covered) / duration,
            hr_max,
            hr_rest,
        )
        if value is not None:
            estimates.append(value)
    return statistics.fmean(estimates) if estimates else None
