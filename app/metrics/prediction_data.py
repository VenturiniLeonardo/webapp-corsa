"""Adapt canonical runs/streams to v2. No provider writes or inferred attestations."""

import math
from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import date, datetime, timedelta
from itertools import pairwise
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.prediction_inputs import ActivityInput, Profile
from app.domain.models import Activity, ActivityMetrics, BestEffort, Stream
from app.domain.stream_codec import decode_stream
from app.metrics.engine import compute_grades, minetti_factor
from app.metrics.predictions_v2 import P, Reference, RunV2, normalized, weighted_quantile


def segment(
    ch: dict[str, list[Any]], offset: float, duration: float, warmup: float = 0
) -> dict[str, Any]:
    """Time-weighted, covered 30-second medians; missing intervals aren't filled."""
    ts, ds, hrs = ch.get("time", []), ch.get("distance", []), ch.get("hr", [])
    if (
        len(ts) < 2
        or len(ds) != len(ts)
        or any(not isinstance(x, (int, float)) or not math.isfinite(x) for x in ts + ds)
        or any(b < a for a, b in pairwise(ts))
        or any(b < a for a, b in pairwise(ds))
    ):
        return {}
    start, end = ts[0] + offset + warmup, ts[0] + offset + duration
    if end <= start:
        return {}
    lo, hi = max(0, bisect_right(ts, start) - 1), bisect_left(ts, end)
    hi = min(hi, len(ts) - 1)
    altitude = ch.get("altitude", [])
    grade = (
        compute_grades(ds, altitude)
        if len(altitude) == len(ts)
        and all(x is None or isinstance(x, (int, float)) and math.isfinite(x) for x in altitude)
        else []
    )
    windows: dict[int, list[tuple[float, float, float]]] = defaultdict(list)
    covered, moving, distance, known_grade, gap_distance = 0.0, 0.0, 0.0, 0.0, 0.0
    for i in range(lo, hi):
        dt_raw = ts[i + 1] - ts[i]
        if dt_raw <= 0 or dt_raw > 10:
            continue
        left, right = max(start, ts[i]), min(end, ts[i + 1])
        dt = right - left
        if dt <= 0:
            continue
        dd = (ds[i + 1] - ds[i]) * dt / dt_raw
        v = dd / dt
        if not math.isfinite(v) or v < 0 or v > 8:
            continue
        covered += dt
        if v >= 0.5:
            moving += dt
        distance += dd
        g = grade[i + 1] if grade else None
        if g is not None and abs(g) < 0.45:
            known_grade += dd
            gap_distance += dd * minetti_factor(g)
        hr = hrs[i] if i < len(hrs) else None
        if not isinstance(hr, (int, float)) or not math.isfinite(hr) or not 40 <= hr <= 220:
            continue
        # Split at the exact 30-second boundary rather than weighting a window
        # with points lying in its neighbour.
        cursor = left
        while cursor < right:
            slot = int((cursor - start) // 30)
            stop = min(right, start + (slot + 1) * 30)
            windows[slot].append((v, float(hr), stop - cursor))
            cursor = stop
    span = end - start
    if covered < 0.8 * span:
        return {}
    vs, hs, ws = [], [], []
    for slot, values in windows.items():
        width = min(30, span - slot * 30)
        weight = sum(row[2] for row in values)
        if weight < 0.8 * width:
            continue
        vs.append(weighted_quantile([r[0] for r in values], [r[2] for r in values]))
        hs.append(weighted_quantile([r[1] for r in values], [r[2] for r in values]))
        ws.append(weight)
    valid_hr = sum(ws) >= 0.8 * span
    g = gap_distance / known_grade if distance > 0 and known_grade >= 0.8 * distance else None
    if g is not None and not 0.9 <= g <= 1.15:
        g = None
    velocity = weighted_quantile(vs, ws) if valid_hr else None
    return {
        "hr": weighted_quantile(hs, ws) if valid_hr else None,
        "speed": velocity,
        "gap_speed": velocity * g if velocity and g else None,
        "grade_factor": g,
        "moving_s": moving,
        "duration_s": span,
    }


def load_data(
    db: Session, today: date, inputs: dict[str, Any], cutoff: datetime
) -> tuple[list[RunV2], list[Reference], list[Activity], list[str]]:
    profile = Profile.model_validate(inputs.get("profile", {}))
    meta = {
        int(k): ActivityInput.model_validate(v) for k, v in inputs.get("activities", {}).items()
    }
    rows = db.execute(
        select(Activity, ActivityMetrics)
        .outerjoin(ActivityMetrics, Activity.id == ActivityMetrics.activity_id)
        .where(
            Activity.excluded_from_stats.is_(False),
            Activity.duplicate_of_id.is_(None),
            Activity.upstream_deleted_at.is_(None),
            Activity.is_indoor.is_not(True),
            Activity.sport_type == "run",
            Activity.local_date <= today.isoformat(),
            Activity.start_time_utc <= cutoff.isoformat(),
            Activity.local_date >= (today - timedelta(days=421)).isoformat(),
            Activity.distance_m >= 1000,
            Activity.elapsed_s > 0,
        )
        .order_by(Activity.local_date, Activity.id)
    ).all()
    efforts: dict[int, list[BestEffort]] = defaultdict(list)
    for effort in db.scalars(
        select(BestEffort).where(BestEffort.distance_m >= 3000, BestEffort.elapsed_s >= 600)
    ):
        efforts[effort.activity_id].append(effort)
    runs, refs, activities, notices = [], [], [], []
    excluded_tests = 0
    for a, m in rows:
        info = meta.get(a.id, ActivityInput.model_validate({}))
        official = bool(a.workout_type == "race" and info.official_verified)
        suspect = bool(m and m.gps_suspect)
        if suspect and not official:
            continue
        d, elapsed = float(a.distance_m or 0), float(a.elapsed_s or 0)
        moving = float(a.moving_s or elapsed)
        if not all(math.isfinite(x) and x > 0 for x in (d, elapsed, moving)):
            continue
        if not official and not 1 <= d / moving <= 8:
            continue
        streams: dict[str, list[Any]] = {}
        needs_stream = (
            (today - date.fromisoformat(a.local_date)).days < 56
            or a.workout_type == "race"
            or a.id in efforts
        )
        if needs_stream and not suspect:
            sid = a.stream_source_id or a.primary_source_id
            st = db.get(Stream, sid) if sid else None
            if st:
                try:
                    streams = decode_stream(st.data)
                except (ValueError, OSError, EOFError):
                    pass
        mismatch = bool(
            official and info.official_distance_m and abs(d / info.official_distance_m - 1) > 0.02
        )
        if mismatch:
            streams = {}
        full = segment(streams, 0, elapsed) if streams else {}
        analysed = segment(streams, 0, elapsed, 300) if streams and m and m.is_steady else {}
        sensor = info.sensor or profile.sensor
        run = RunV2(
            a.id,
            date.fromisoformat(a.local_date),
            float(info.official_distance_m) if official and info.official_distance_m else d,
            elapsed,
            moving,
            analysed.get("hr", a.avg_hr),
            bool(m and m.is_steady),
            analysed.get("gap_speed")
            if analysed
            else (m.gap_speed_ms if m and not suspect and not mismatch else None),
            a.weather_temp_c,
            a.weather_dew_point_c,
            a.workout_type == "race",
            sensor,
            info.sensor_id
            or (profile.sensor_id if not info.sensor or info.sensor == profile.sensor else None),
            analysed.get("duration_s"),
            bool(analysed.get("hr")),
            analysed.get("speed"),
        )
        runs.append(run)
        activities.append(a)
        if run.race and (today - run.day).days <= 365 and (info.official_distance_m or d) >= 3000:
            ref_d = info.official_distance_m if official else d
            ref_t = info.official_elapsed_s if official and info.official_elapsed_s else elapsed
            ref = Reference(
                run,
                ref_d or d,
                ref_t,
                full.get("moving_s", moving) if ref_t == elapsed else None,
                full.get("grade_factor"),
                info.flat_confirmed,
                official,
                info.baseline_volume_km,
                info.baseline_long_km,
            )
            refs.append(ref)
            continue
        if run.race:
            continue
        candidates = []
        for e in efforts.get(a.id, []):
            if e.elapsed_s > elapsed or e.distance_m > d * 1.02:
                continue
            part = (
                segment(streams, float(e.start_offset_s or 0), float(e.elapsed_s))
                if streams
                else {}
            )
            continuous = part.get("moving_s", 0) >= e.elapsed_s / 1.05
            hr = part.get("hr")
            intensity = ""
            if continuous and hr is not None and sensor != "unknown":
                long = e.elapsed_s > 3600
                if profile.hr_threshold:
                    if hr / profile.hr_threshold >= (0.9 if long else 0.95):
                        intensity = "threshold_hr"
                elif profile.hr_max_measured and hr / profile.hr_max_measured >= (
                    0.85 if long else 0.9
                ):
                    intensity = "measured_max_hr"
            confirmed = (
                info.test_verified
                and info.test_distance_m == e.distance_m
                and (info.test_rpe or 0) >= 9
            )
            if not intensity and confirmed and (continuous or not part):
                intensity = "declared_continuous_test_rpe"
            if not intensity:
                continue
            ref = Reference(
                run,
                e.distance_m,
                float(e.elapsed_s),
                part.get("moving_s"),
                part.get("grade_factor"),
                info.flat_confirmed,
                False,
                info.baseline_volume_km,
                info.baseline_long_km,
                float(e.start_offset_s or 0),
                intensity,
            )
            score = normalized(ref)[0] * (10000 / e.distance_m) ** P.k0
            candidates.append((score, -e.distance_m, e.start_offset_s or 0, e.id, ref))
        if candidates:
            refs.append(min(candidates, key=lambda c: c[:4])[-1])
        elif efforts.get(a.id):
            excluded_tests += 1
    if excluded_tests:
        notices.append(
            f"{excluded_tests} allenamenti con best effort non qualificati per intensità: esclusi dai riferimenti."
        )
    return runs, refs, activities, notices
