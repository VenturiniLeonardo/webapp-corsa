"""Race model v2: pure, deterministic, auditable; initial parameters are heuristic.

See docs/RACE-PREDICTIONS-V2.md. Keep v1 intact for regression/backtest comparisons.
All times/pace outputs retain precision; formatting belongs at the UI boundary.
"""

import math
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from itertools import pairwise
from typing import Any

from app.metrics.engine import HEAT_TABLE
from app.metrics.predictions import DISTANCES, Run

VERSION = "riegel-hr-weather-v2"
PARAMETER_VERSION = "initial-2026-10-19"


@dataclass(frozen=True)
class Parameters:
    """PARAMETRO_DA_CALIBRARE: frozen initial values; not individually fitted."""

    max_age_days: int = 365
    recent_race_days: int = 180
    half_life_days: float = 56
    min_runs: int = 3
    min_coverage: float = 0.8
    k0: float = 1.06
    full_ratio: float = 2
    ramp_half_start_m: float = 10000
    ramp_marathon_start_m: float = 21097.5
    half_volume_km: float = 30
    half_long_km: float = 18
    marathon_volume_km: float = 55
    marathon_long_km: float = 30
    half_coefficient: float = 0.03
    marathon_coefficient: float = 0.06
    detraining_volume: float = 0.12
    detraining_long: float = 0.06
    detraining_transfer: tuple[float, ...] = (0.35, 0.55, 0.8, 1.0)
    hr_transfer: tuple[float, ...] = (0.25, 0.5, 0.75, 1.0)
    hr_pair_days: int = 7
    hr_delta: float = 10
    temp_delta: float = 3
    dew_delta: float = 3
    hr_support_prior: float = 12
    hr_span_prior: float = 21
    hr_cap: float = 0.08
    optical_quality: float = 0.5
    unknown_quality: float = 0.25
    training_quality: float = 0.3
    race_mass_floor: float = 0.5
    heat_max: float = 0.2
    heat_reference_s: float = 3600
    heat_gamma: float = 0.5
    heat_scale_min: float = 0.5
    heat_scale_max: float = 1.5
    base_error: float = 0.04
    age_error: float = 0.06
    training_error: float = 0.06
    hr_error: float = 0.02
    endurance_error: float = 0.03
    endurance_fraction: float = 0.75
    marathon_error: float = 0.04
    marathon_preparation_error: float = 0.05
    consolidated_volume_km: float = 40
    consolidated_long_km: float = 28
    missing_weather_error: float = 0.01
    missing_grade_error: float = 0.03
    missing_baseline_error: float = 0.01
    dispersion_error: float = 1
    slow_tail: tuple[float, ...] = (1.2, 1.3, 1.5, 1.8)
    medium_error: float = 0.1
    recent_race_mass: float = 0.5
    coherence_limit: float = 0.02
    taper_days: int = 21


P = Parameters()


@dataclass(frozen=True)
class RunV2(Run):
    sensor: str = "unknown"
    sensor_id: str | None = None
    hr_duration_s: float | None = None
    hr_from_stream: bool = False
    analysed_speed_ms: float | None = None


@dataclass(frozen=True)
class Reference:
    run: RunV2
    distance_m: float
    elapsed_s: float
    moving_s: float | None = None
    grade_factor: float | None = None
    flat_confirmed: bool = False
    official: bool = False
    baseline_volume_km: float | None = None
    baseline_long_km: float | None = None
    start_offset_s: float = 0
    intensity_method: str = "race"


def clip(value: float, lo: float = 0, hi: float = 1) -> float:
    return max(lo, min(hi, value))


def weighted_quantile(values: list[float], weights: list[float], q: float = 0.5) -> float:
    """Midpoint of the median interval; empirical inverse-CDF for other quantiles."""
    grouped: dict[float, float] = {}
    for value, weight in zip(values, weights, strict=True):
        if math.isfinite(value) and math.isfinite(weight) and weight > 0:
            grouped[value] = grouped.get(value, 0) + weight
    if not grouped:
        raise ValueError("No positive finite weights")
    ordered = sorted(grouped.items())
    total = sum(grouped.values())
    cumulative = 0.0
    for i, (value, weight) in enumerate(ordered):
        cumulative += weight / total
        if q == 0.5 and abs(cumulative - q) <= 1e-12 and i + 1 < len(ordered):
            return (value + ordered[i + 1][0]) / 2
        if cumulative >= q:
            return value
    return ordered[-1][0]


def pava(values: list[float], weights: list[float]) -> list[float]:
    """Weighted L2 isotonic projection. Inputs are log-pace, not rounded pace."""
    blocks: list[tuple[int, int, float, float]] = []
    for i, (value, weight) in enumerate(zip(values, weights, strict=True)):
        blocks.append((i, i + 1, weight, value * weight))
        while len(blocks) > 1 and blocks[-2][3] / blocks[-2][2] > blocks[-1][3] / blocks[-1][2]:
            right, left = blocks.pop(), blocks.pop()
            blocks.append((left[0], right[1], left[2] + right[2], left[3] + right[3]))
    result = [0.0] * len(values)
    for lo, hi, weight, total in blocks:
        result[lo:hi] = [total / weight] * (hi - lo)
    return result


def widen_monotone(lower: list[float], upper: list[float]) -> tuple[list[float], list[float]]:
    return (
        [min(lower[i:]) for i in range(len(lower))],
        [max(upper[: i + 1]) for i in range(len(upper))],
    )


def heat_fraction(temp: float | None, dew: float | None, p: Parameters = P) -> float | None:
    if temp is None or dew is None or not math.isfinite(temp + dew) or dew > temp:
        return None
    x = 1.8 * (temp + dew) + 64
    if x <= 100:
        return 0.0
    if x > 180:
        return min(p.heat_max, 0.1 + 0.002 * (x - 180))
    for (a, ha), (b, hb) in pairwise(HEAT_TABLE):
        if a <= x <= b:
            return (ha + (hb - ha) * (x - a) / (b - a)) / 100
    return None


def heat_factor(
    temp: float | None, dew: float | None, duration_s: float, p: Parameters = P
) -> float:
    fraction = heat_fraction(temp, dew, p)
    scale = clip(
        (duration_s / p.heat_reference_s) ** p.heat_gamma, p.heat_scale_min, p.heat_scale_max
    )
    return 1 + (fraction or 0) * scale


def coverage(start: date, end: date, intervals: list[tuple[date, date]]) -> float:
    days = (end - start).days + 1
    if days <= 0:
        return 0.0
    return (
        sum(any(lo <= start + timedelta(days=i) <= hi for lo, hi in intervals) for i in range(days))
        / days
    )


def preparation(runs: list[RunV2], end: date) -> tuple[float, float]:
    selected = [r for r in runs if 0 <= (end - r.day).days < 56]
    return sum(r.distance_m for r in selected) / 8000, max(
        (r.distance_m for r in selected), default=0
    ) / 1000


def trend(runs: list[RunV2], p: Parameters = P) -> dict[str, Any]:
    usable = [
        r
        for r in runs
        if r.steady
        and r.hr is not None
        and 40 <= r.hr <= 220
        and 1200 <= r.moving_s <= 10800
        and r.elapsed_s <= 1.05 * r.moving_s
        and heat_fraction(r.temp_c, r.dew_c, p) is not None
    ]
    slopes: list[float] = []
    weights: list[float] = []
    days: list[date] = []
    qualities = {"chest": 1.0, "optical": p.optical_quality, "unknown": p.unknown_quality}
    for i, a in enumerate(usable):
        for b in usable[i + 1 :]:
            delta = (b.day - a.day).days
            da, db = a.hr_duration_s or a.moving_s, b.hr_duration_s or b.moving_s
            same_sensor = a.sensor == b.sensor and (
                a.sensor_id == b.sensor_id if a.sensor_id and b.sensor_id else True
            )
            if abs(delta) < p.hr_pair_days or not same_sensor or not 0.5 <= db / da <= 2:
                continue
            if (a.gap_speed_ms is None) != (b.gap_speed_ms is None):
                continue
            qa = qualities[a.sensor] if a.hr_from_stream else p.unknown_quality
            qb = qualities[b.sensor] if b.hr_from_stream else p.unknown_quality
            weight = qa * qb * max(0, 1 - abs((a.hr or 0) - (b.hr or 0)) / p.hr_delta)
            weight *= max(0, 1 - abs((a.temp_c or 0) - (b.temp_c or 0)) / p.temp_delta)
            weight *= max(0, 1 - abs((a.dew_c or 0) - (b.dew_c or 0)) / p.dew_delta)
            if weight <= 0:
                continue
            ae = (
                (a.gap_speed_ms or a.analysed_speed_ms or a.distance_m / a.moving_s)
                * heat_factor(a.temp_c, a.dew_c, da, p)
                / (a.hr or 1)
            )
            be = (
                (b.gap_speed_ms or b.analysed_speed_ms or b.distance_m / b.moving_s)
                * heat_factor(b.temp_c, b.dew_c, db, p)
                / (b.hr or 1)
            )
            if ae <= 0 or be <= 0:
                continue
            slopes.append(math.log(be / ae) / delta)
            weights.append(weight)
            days.extend((a.day, b.day))
    support = sum(weights)
    span = (max(days) - min(days)).days if days else 0
    rho = support / (support + p.hr_support_prior) * span / (span + p.hr_span_prior)
    cap = math.log1p(p.hr_cap) / 56
    slope = clip(weighted_quantile(slopes, weights), -cap, cap) if slopes else 0.0
    return {
        "slope_per_day": slope,
        "reliability": rho,
        "support": support,
        "span_days": span,
        "pairs": len(weights),
        "runs": len(usable),
    }


def normalized(ref: Reference, p: Parameters = P) -> tuple[float, list[str]]:
    flags = []
    g = ref.grade_factor
    if g is None or not 0.9 <= g <= 1.15:
        g = 1.0
        if not ref.flat_confirmed:
            flags.append("grade_missing")
    if ref.moving_s is not None and 0 < ref.moving_s <= ref.elapsed_s:
        flat = ref.moving_s / g + (ref.elapsed_s - ref.moving_s)
    else:
        flat = ref.elapsed_s / g
        if g != 1:
            flags.append("stops_unknown")
    if heat_fraction(ref.run.temp_c, ref.run.dew_c, p) is None:
        flags.append("weather_missing")
    return flat / heat_factor(ref.run.temp_c, ref.run.dew_c, ref.elapsed_s, p), flags


def exponent(
    distance: float, reference: float, volume: float, longest: float, p: Parameters = P
) -> float:
    g = clip(math.log(distance / reference) / math.log(p.full_ratio))
    half = clip((distance - p.ramp_half_start_m) / (21097.5 - p.ramp_half_start_m))
    marathon = clip((distance - p.ramp_marathon_start_m) / (42195 - p.ramp_marathon_start_m))
    ph = p.half_coefficient * (
        max(0, 1 - volume / p.half_volume_km) + max(0, 1 - longest / p.half_long_km)
    )
    pm = p.marathon_coefficient * (
        max(0, 1 - volume / p.marathon_volume_km) + max(0, 1 - longest / p.marathon_long_km)
    )
    return p.k0 + g * (half * ph + marathon * pm)


def predict_v2(
    runs: list[RunV2],
    references: list[Reference],
    today: date,
    intervals: list[tuple[date, date]] | None = None,
    taper_start: date | None = None,
    p: Parameters = P,
) -> dict[str, Any]:
    intervals = intervals or []
    recent = [r for r in runs if 0 <= (today - r.day).days < 56]
    volume, longest = preparation(runs, today)
    current_coverage = coverage(today - timedelta(days=55), today, intervals)
    dt_end = (
        taper_start - timedelta(days=1)
        if taper_start and 0 <= (today - taper_start).days <= p.taper_days
        else today
    )
    dt_v, dt_l = preparation(runs, dt_end)
    dt_known = coverage(dt_end - timedelta(days=55), dt_end, intervals) >= p.min_coverage
    tr = trend(recent, p)
    refs = [r for r in references if 0 <= (today - r.run.day).days <= p.max_age_days]
    races = [r for r in refs if r.run.race]
    if any((today - r.run.day).days < p.recent_race_days for r in races):
        refs = races
    notices = []
    if current_coverage < p.min_coverage:
        notices.append(
            "Completezza dello storico non confermata: confronto della preparazione limitato."
        )
    if not tr["pairs"]:
        notices.append(
            "Nessun confronto FC con sensore e meteo omogenei: correzione di forma neutra."
        )
    if not refs or len(recent) < p.min_runs:
        notices.append(
            "Servono almeno 3 corse nelle ultime 8 settimane e una gara o un test qualificato nell'ultimo anno."
        )
    items: list[dict[str, Any]] = []
    for index, (distance, label) in enumerate(DISTANCES):
        if not refs or len(recent) < p.min_runs:
            items.append(
                {
                    "distance_m": distance,
                    "label": label,
                    "elapsed_s": None,
                    "pace_s_per_km": None,
                    "pace_low_s_per_km": None,
                    "pace_high_s_per_km": None,
                    "confidence": "unavailable",
                    "reasons": ["Dati insufficienti."],
                    "sources": [],
                    "anchor_activity_id": None,
                }
            )
            continue
        sources = []
        for ref in refs:
            base, flags = normalized(ref, p)
            age = (today - ref.run.day).days
            end = ref.run.day - timedelta(days=1)
            bv, bl = preparation(runs, end)
            baseline_known = coverage(end - timedelta(days=55), end, intervals) >= p.min_coverage
            known_v = ref.baseline_volume_km is not None or baseline_known
            known_l = ref.baseline_long_km is not None or baseline_known
            bv = ref.baseline_volume_km if ref.baseline_volume_km is not None else bv
            bl = ref.baseline_long_km if ref.baseline_long_km is not None else bl
            if not dt_known or not known_v or not known_l or bv <= 0 or bl <= 0:
                flags.append("baseline_missing")
            rv = max(0, 1 - dt_v / bv) if dt_known and known_v and bv > 0 else 0
            rl = max(0, 1 - dt_l / bl) if dt_known and known_l and bl > 0 else 0
            q = 1 + p.detraining_transfer[index] * (
                p.detraining_volume * rv + p.detraining_long * rl
            )
            f = math.exp(
                p.hr_transfer[index] * tr["reliability"] * tr["slope_per_day"] * min(age, 56)
            )
            correction = q / f if f >= 1 else max(q, 1 / f)
            k = exponent(distance, ref.distance_m, volume, longest, p)
            seconds = base * (distance / ref.distance_m) ** k * correction
            weight = 2 ** (-age / p.half_life_days) * math.exp(
                -abs(math.log(distance / ref.distance_m))
            )
            weight *= 1 if ref.run.race else p.training_quality
            sources.append(
                {
                    "activity_id": ref.run.id,
                    "date": ref.run.day.isoformat(),
                    "kind": "race" if ref.run.race else "test",
                    "distance_m": ref.distance_m,
                    "elapsed_s": ref.elapsed_s,
                    "standard_reference_s": base,
                    "candidate_s": seconds,
                    "age_days": age,
                    "weight": weight,
                    "hr_factor": f,
                    "preparation_factor": q,
                    "k": k,
                    "flags": flags,
                    "official": ref.official,
                    "intensity_method": ref.intensity_method,
                }
            )
        total = sum(s["weight"] for s in sources)
        race_weight = sum(s["weight"] for s in sources if s["kind"] == "race")
        train_weight = total - race_weight
        if 0 < race_weight < p.race_mass_floor * total and train_weight > 0:
            for s in sources:
                s["weight"] *= (
                    p.race_mass_floor / race_weight
                    if s["kind"] == "race"
                    else (1 - p.race_mass_floor) / train_weight
                )
        else:
            for s in sources:
                s["weight"] /= total
        weights = [s["weight"] for s in sources]
        times = [s["candidate_s"] for s in sources]
        raw = weighted_quantile(times, weights)
        age_mean = sum(s["weight"] * s["age_days"] for s in sources)
        train_mass = sum(s["weight"] for s in sources if s["kind"] == "test")
        spread = weighted_quantile([abs(math.log(t / raw)) for t in times], weights)
        missing = {
            flag: sum(s["weight"] for s in sources if flag in s["flags"])
            for flag in ("weather_missing", "grade_missing", "baseline_missing")
        }
        err = p.base_error + p.age_error * min(age_mean / p.max_age_days, 1)
        err += p.training_error * train_mass + p.hr_error * (1 - tr["reliability"])
        err += (
            p.endurance_error * max(0, 1 - longest / (p.endurance_fraction * distance / 1000))
            if distance >= 21097.5
            else 0
        )
        if distance == 42195:
            err += p.marathon_error
            if volume < p.consolidated_volume_km or longest < p.consolidated_long_km:
                err += p.marathon_preparation_error
        err += p.missing_weather_error * missing["weather_missing"]
        err += p.missing_grade_error * missing["grade_missing"]
        err += p.missing_baseline_error * missing["baseline_missing"] + p.dispersion_error * spread
        recent_mass = sum(
            s["weight"] for s in sources if s["kind"] == "race" and s["age_days"] < 56
        )
        reasons = []
        if missing["grade_missing"]:
            reasons.append("Pendenza non corretta per una parte dei riferimenti.")
        if age_mean >= 56:
            reasons.append("Riferimenti prevalentemente anteriori alle ultime 8 settimane.")
        if train_mass:
            reasons.append("Include test qualificati in allenamento.")
        if distance == 42195 and (volume < 40 or longest < 28):
            reasons.append("Preparazione maratona non consolidata: stima esplorativa.")
        medium = (
            err <= p.medium_error
            and recent_mass >= p.recent_race_mass
            and not missing["grade_missing"]
            and current_coverage >= p.min_coverage
        )
        source = min(sources, key=lambda s: (-s["weight"], s["age_days"], s["activity_id"]))
        items.append(
            {
                "distance_m": distance,
                "label": label,
                "elapsed_s": raw,
                "raw_elapsed_s": raw,
                "pace_s_per_km": raw / (distance / 1000),
                "confidence": "medium" if medium else "low",
                "reasons": reasons,
                "anchor_activity_id": source["activity_id"],
                "sources": sources,
                "weighted_age_days": age_mean,
                "training_mass": train_mass,
                "effective_sources": 1 / sum(w * w for w in weights),
                "dispersion_log": spread,
                "base_margin_log": err,
                "coherence_change_pct": 0.0,
            }
        )
    valid = [item for item in items if item["elapsed_s"] is not None]
    if valid:
        ys = [math.log(item["pace_s_per_km"]) for item in valid]
        ws = [1 / item["base_margin_log"] ** 2 for item in valid]
        zs = pava(ys, ws)
        lower, upper = [], []
        for item, y, z in zip(valid, ys, zs, strict=True):
            pace = math.exp(z)
            err = item["base_margin_log"] + abs(z - y)
            item["coherence_change_pct"] = math.expm1(z - y) * 100
            if abs(math.expm1(z - y)) > p.coherence_limit:
                item["confidence"] = "low"
                item["reasons"].append("Correzione di coerenza fra distanze significativa.")
            item["elapsed_s"] = pace * item["distance_m"] / 1000
            item["pace_s_per_km"] = pace
            index = list(DISTANCES).index((item["distance_m"], item["label"]))
            lower.append(pace * math.exp(-err))
            upper.append(pace * math.exp(p.slow_tail[index] * err))
        lower, upper = widen_monotone(lower, upper)
        for item, lo, hi in zip(valid, lower, upper, strict=True):
            item.update(
                pace_low_s_per_km=lo,
                pace_high_s_per_km=hi,
                elapsed_low_s=lo * item["distance_m"] / 1000,
                elapsed_high_s=hi * item["distance_m"] / 1000,
            )
    return {
        "model_version": VERSION,
        "parameter_version": PARAMETER_VERSION,
        "parameters": asdict(p),
        "items": items,
        "notices": notices,
        "runs_8w": len(recent),
        "hr_runs_8w": sum(r.hr is not None for r in recent),
        "steady_hr_runs_8w": tr["runs"],
        "hr_comparable_pairs": tr["pairs"],
        "hr_trend_applied": bool(tr["pairs"]),
        "hr_trend": tr,
        "hr_efficiency_change_pct": math.expm1(tr["slope_per_day"] * 56) * 100
        if tr["pairs"]
        else None,
        "avg_weekly_distance_m": volume * 1000,
        "longest_run_m": longest * 1000,
        "coverage_fraction": current_coverage,
        "taper_applied": dt_end != today,
    }


def apply_weather(
    items: list[dict[str, Any]], weather_by_distance: list[dict[str, Any]], p: Parameters = P
) -> None:
    """Add a separate scenario, then reconcile its points; never narrow intervals."""
    valid = []
    for index, (item, weather) in enumerate(zip(items, weather_by_distance, strict=True)):
        samples = weather.get("samples", [])
        if item["elapsed_s"] is None or not samples:
            item["scenario"] = None
            continue
        hs = [heat_factor(t, d, item["elapsed_s"], p) for t, d, _ in samples]
        weights = [w for _, _, w in samples]
        med = weighted_quantile(hs, weights)
        lo, hi = weighted_quantile(hs, weights, 0.1), weighted_quantile(hs, weights, 0.9)
        if weather["status"] == "forecast":
            lo, hi = max(1, med * math.exp(-0.01)), med * math.exp(0.02)
        extreme = any(t < 0 or 1.8 * (t + d) + 64 > 180 for t, d, _ in samples)
        if extreme:
            hi *= math.exp(0.03)
        pace = item["pace_s_per_km"] * med
        item["scenario"] = {
            "pace_s_per_km": pace,
            "pace_low_s_per_km": item["pace_low_s_per_km"] * lo,
            "pace_high_s_per_km": item["pace_high_s_per_km"] * hi,
            "elapsed_s": pace * item["distance_m"] / 1000,
            "heat_adjustment_pct": (med - 1) * 100,
            "confidence": "low" if extreme else item["confidence"],
            "weather_status": weather["status"],
            "extreme": extreme,
            "temp_c": weighted_quantile([t for t, _, _ in samples], weights),
            "dew_point_c": weighted_quantile([d for _, d, _ in samples], weights),
        }
        valid.append((index, item))
    if not valid:
        return
    ys = [math.log(item["scenario"]["pace_s_per_km"]) for _, item in valid]
    zs = pava(ys, [1 / item["base_margin_log"] ** 2 for _, item in valid])
    lows, highs = [], []
    for (_, item), y, z in zip(valid, ys, zs, strict=True):
        scenario = item["scenario"]
        pace = math.exp(z)
        if abs(math.expm1(z - y)) > p.coherence_limit:
            scenario["confidence"] = "low"
        scenario.update(
            pace_s_per_km=pace,
            elapsed_s=pace * item["distance_m"] / 1000,
            coherence_change_pct=math.expm1(z - y) * 100,
        )
        lows.append(min(pace, scenario["pace_low_s_per_km"]))
        highs.append(max(pace, scenario["pace_high_s_per_km"]))
    lows, highs = widen_monotone(lows, highs)
    for (_, item), lo, hi in zip(valid, lows, highs, strict=True):
        item["scenario"].update(
            pace_low_s_per_km=lo,
            pace_high_s_per_km=hi,
            elapsed_low_s=lo * item["distance_m"] / 1000,
            elapsed_high_s=hi * item["distance_m"] / 1000,
        )
