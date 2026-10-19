"""Explainable race estimates, not a validated individual physiological model.

Riegel anchors + robust HR efficiency trend + existing Hadley heat heuristic.
No fixed %HRmax is extrapolated into a race pace. All preparation coefficients
and displayed ranges are conservative heuristics, not statistical intervals.
"""

import math
import statistics
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.metrics.engine import heat_slowdown_pct

DISTANCES = ((5000.0, "5 km"), (10000.0, "10 km"), (21097.5, "21,1 km"), (42195.0, "42,2 km"))


@dataclass(frozen=True)
class Run:
    id: int
    day: date
    distance_m: float
    elapsed_s: float
    moving_s: float
    hr: float | None = None
    steady: bool = False
    gap_speed_ms: float | None = None
    temp_c: float | None = None
    dew_c: float | None = None
    race: bool = False


@dataclass(frozen=True)
class Anchor:
    run: Run
    distance_m: float
    elapsed_s: float
    race: bool


def _median(values: list[tuple[float, float]]) -> float:
    ordered = sorted(values)
    half = sum(w for _, w in ordered) / 2
    total = 0.0
    for v, w in ordered:
        total += w
        if total >= half:
            return v
    return ordered[-1][0]


def _heat(r: Run) -> float:
    return 1 + heat_slowdown_pct(r.temp_c, r.dew_c) / 100


def hr_trend(runs: list[Run]) -> tuple[float, int, int, bool]:
    """Median log-EF slope/day from comparable steady runs, equal weight/run.

    Compare only similar HR (±5 bpm), duration (×0.5..2), and weather coverage.
    GAP removes much of the terrain effect; without it only compare raw speeds.
    At least 8 runs, 21 days and 12 comparable pairs; cap change at 8%/56 days.
    """
    usable = [
        r
        for r in runs
        if r.steady
        and r.hr is not None
        and 90 <= r.hr <= 180
        and 1200 <= r.moving_s <= 10800
        and r.distance_m > 0
        and r.elapsed_s <= r.moving_s * 1.05
    ]
    slopes = []
    for i, a in enumerate(usable):
        for b in usable[i + 1 :]:
            days = (b.day - a.day).days
            if abs(days) < 7 or abs((a.hr or 0) - (b.hr or 0)) > 5:
                continue
            if not 0.5 <= b.moving_s / a.moving_s <= 2:
                continue
            if (a.gap_speed_ms is None) != (b.gap_speed_ms is None):
                continue
            if (a.temp_c is not None and a.dew_c is not None) != (
                b.temp_c is not None and b.dew_c is not None
            ):
                continue
            av = a.gap_speed_ms or a.distance_m / a.moving_s
            bv = b.gap_speed_ms or b.distance_m / b.moving_s
            ae = av * _heat(a) / (a.hr or 1)
            be = bv * _heat(b) / (b.hr or 1)
            if ae > 0 and be > 0:
                slopes.append(math.log(be / ae) / days)
    span = (max(r.day for r in usable) - min(r.day for r in usable)).days if usable else 0
    if len(usable) < 8 or span < 21 or len(slopes) < 12:
        return 0.0, len(usable), len(slopes), False
    cap = math.log(1.08) / 56
    return max(-cap, min(cap, statistics.median(slopes))), len(usable), len(slopes), True


def predict(
    runs: list[Run],
    anchors: list[Anchor],
    today: date,
    current_temp_c: float | None = None,
    current_dew_c: float | None = None,
) -> dict[str, Any]:
    recent = [r for r in runs if 0 <= (today - r.day).days < 56]
    weekly = sum(r.distance_m for r in recent) / 8
    longest = max((r.distance_m for r in recent), default=0)
    hr_runs = sum(r.hr is not None for r in recent)
    slope, steady_n, pairs, hr_applied = hr_trend(recent)
    # Historical anchors expire after a year; recent races dominate through a 56-day half-life.
    eligible = [a for a in anchors if 0 <= (today - a.run.day).days <= 365]
    races = [a for a in eligible if a.race]
    selected = races if any((today - a.run.day).days < 180 for a in races) else eligible
    notices = []
    if not hr_applied:
        notices.append(
            "FC: dati regolari confrontabili insufficienti; nessuna correzione di forma."
        )
    if current_temp_c is None or current_dew_c is None:
        notices.append("Meteo attuale non disponibile: stime in condizioni fresche di riferimento.")
    if not selected or len(recent) < 3:
        notices.append(
            "Servono almeno 3 corse nelle ultime 8 settimane e una gara o prova utile nell'ultimo anno."
        )
    items = []
    heat = 1 + heat_slowdown_pct(current_temp_c, current_dew_c) / 100
    for dist, label in DISTANCES:
        if not selected or len(recent) < 3:
            items.append(
                {
                    "distance_m": dist,
                    "label": label,
                    "elapsed_s": None,
                    "pace_s_per_km": None,
                    "pace_low_s_per_km": None,
                    "pace_high_s_per_km": None,
                    "confidence": "unavailable",
                    "reasons": ["Dati insufficienti."],
                    "anchor_activity_id": None,
                }
            )
            continue
        candidates = []
        for a in selected:
            age = (today - a.run.day).days
            # Longer distances require preparation; never accelerate a known
            # performance because volume is low when projecting down in distance.
            exponent = 1.06
            if dist >= 20000 and dist > a.distance_m * 1.1:
                exponent += 0.03 * max(0, 1 - weekly / 30000)
                exponent += 0.03 * max(0, 1 - longest / 18000)
            if dist >= 40000 and dist > a.distance_m * 1.1:
                exponent += 0.06 * max(0, 1 - weekly / 55000)
                exponent += 0.06 * max(0, 1 - longest / 30000)
            base = a.elapsed_s / _heat(a.run)
            form = math.exp(slope * min(age, 56)) if hr_applied else 1.0
            seconds = base * (dist / a.distance_m) ** exponent / form * heat
            similarity = math.exp(-abs(math.log(dist / a.distance_m)))
            weight = 2 ** (-age / 56) * similarity * (1 if a.race else 0.3)
            candidates.append((seconds, weight, a))
        seconds = _median([(t, w) for t, w, _ in candidates])
        source = min(candidates, key=lambda c: abs(c[0] - seconds))[2]
        age = (today - source.run.day).days
        reasons = []
        error = 0.04 + min(age / 365, 1) * 0.06
        if not source.race:
            error += 0.06
            reasons.append("Riferimento da allenamento: potenziale di gara non verificato.")
        if age >= 56:
            reasons.append("Prestazione di riferimento precedente alle ultime 8 settimane.")
        if not hr_applied:
            error += 0.02
        if dist >= 20000 and longest < dist * 0.75:
            error += 0.03
            reasons.append("Pochi riscontri di tenuta sulla distanza.")
        if dist >= 40000:
            error += 0.04
            if weekly < 40000 or longest < 28000:
                error += 0.05
                reasons.append("Preparazione maratona non consolidata: estrapolazione indicativa.")
        if current_temp_c is None or current_dew_c is None:
            error += 0.02
        confidence = "medium" if error <= 0.10 and source.race and age < 56 else "low"
        pace = seconds / (dist / 1000)
        items.append(
            {
                "distance_m": dist,
                "label": label,
                "elapsed_s": round(seconds),
                "pace_s_per_km": round(pace, 1),
                "pace_low_s_per_km": round(pace * (1 - error), 1),
                "pace_high_s_per_km": round(pace * (1 + error), 1),
                "confidence": confidence,
                "reasons": reasons,
                "anchor_activity_id": source.run.id,
            }
        )
    return {
        "items": items,
        "runs_8w": len(recent),
        "hr_runs_8w": hr_runs,
        "steady_hr_runs_8w": steady_n,
        "hr_comparable_pairs": pairs,
        "hr_trend_applied": hr_applied,
        "hr_efficiency_change_pct": round(math.expm1(slope * 56) * 100, 1) if hr_applied else None,
        "avg_weekly_distance_m": round(weekly),
        "longest_run_m": round(longest),
        "heat_adjustment_pct": round((heat - 1) * 100, 1),
        "notices": notices,
    }
