"""Plain-text report of one activity, written to be pasted into an LLM (web download + Telegram)."""

import math
from datetime import datetime
from statistics import fmean
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.api.activities import Db, LapOut, get_activity
from app.api.settings import _read
from app.domain.models import Stream
from app.domain.stream_codec import decode_stream

router = APIRouter(prefix="/api")
N = float | None


# --- formatting (every missing value is the literal "n/a" so a model never guesses) ---
def _n(x: N, d: int = 0, u: str = "") -> str:
    return "n/a" if x is None or not math.isfinite(x) else f"{x:.{d}f}{u}"


def _hms(s: N) -> str:
    if s is None or not math.isfinite(s):
        return "n/a"
    t = round(s)
    h, m = t // 3600, t % 3600 // 60
    return f"{h}:{m:02d}:{t % 60:02d}" if h else f"{m}:{t % 60:02d}"


def _pace(spk: N) -> str:
    return f"{_hms(spk)}/km" if spk is not None and math.isfinite(spk) and spk > 0 else "n/a"


def _km(m: N) -> str:
    return "n/a" if m is None else f"{m / 1000:.2f}"


def _yn(b: bool | None) -> str:
    return "n/a" if b is None else "yes" if b else "no"


def _date(iso: str, tz: str) -> str:
    return datetime.fromisoformat(iso).astimezone(ZoneInfo(tz)).strftime("%d %b %Y, %H:%M")


def _lap_pace(lp: LapOut) -> float | None:
    if lp.moving_s and lp.distance_m:
        return lp.moving_s * 1000 / lp.distance_m
    return 1000 / lp.avg_speed_ms if lp.avg_speed_ms else None


# --- stats ---
def _q(s: list[float], p: float) -> float:
    return s[int((len(s) - 1) * p)]


def _avg(xs: list[float]) -> float | None:
    return fmean(xs) if xs else None


def _summary(xs: list[float]) -> str:
    if not xs:
        return "n/a"
    s = sorted(xs)
    ps = (("p5", 0.05), ("p25", 0.25), ("median", 0.5), ("p75", 0.75), ("p95", 0.95))
    mid = ", ".join(f"{k} {_n(_q(s, p))}" for k, p in ps)
    return f"min {_n(s[0])}, {mid}, max {_n(s[-1])}"


def build_report(s: Session, aid: int) -> str:
    d, cfg = get_activity(aid, s), _read(s)
    a, m = d.activity, d.metrics
    tz = a.timezone or "UTC"
    sid = a.stream_source_id or a.primary_source_id
    st = s.get(Stream, sid) if sid else None
    ch: dict[str, list[Any]] = decode_stream(st.data) if st else {}
    avg_pace = a.moving_s * 1000 / a.distance_m if a.moving_s and a.distance_m else None
    time = ch.get("time") or []
    t0 = (time[0] if time else 0) or 0

    def seg(name: str, frm: float = 0, to: float = math.inf) -> list[float]:
        """Positive samples of a stream channel within [frm, to) seconds from activity start."""
        ys = ch.get(name) or []
        return [
            y
            for t, y in zip(time, ys, strict=False)
            if t is not None and y is not None and y > 0 and frm <= t - t0 < to
        ]

    hr, cad = seg("hr"), seg("cadence")
    alt = [x for x in ch.get("altitude") or [] if x is not None]
    hr_max = cfg["hr_max"]
    L: list[str] = []

    def sec(t: str) -> None:
        L.extend(["", f"## {t}"])

    L += [
        "# RUNNING ACTIVITY REPORT",
        (
            "Tags: (measured) = sensor, (calc) = derived by math, (est.) = estimated by "
            "provider/app, (model) = model output."
        ),
    ]

    sec("OVERVIEW")
    diff = f"{a.difficulty}/10" if a.difficulty is not None else "n/a"
    L += [
        f"Name: {a.name or 'n/a'}",
        f"Start (local, {tz}): {_date(a.start_time_utc, tz)}",
        (
            f"Sport: {a.sport_type}; workout type: {a.workout_type or 'n/a'}; "
            f"perceived difficulty: {diff}"
        ),
        f"Tags: {', '.join(d.tags) or 'none'}",
        f"Notes: {(a.notes or '').strip() or 'none'}",
    ]

    sec("TOTALS")
    stopped = (
        _hms(a.elapsed_s - a.moving_s)
        if a.elapsed_s is not None and a.moving_s is not None
        else "n/a"
    )
    speed = _n(3.6 / (avg_pace / 1000), 2, " km/h") if avg_pace else "n/a"
    alt_mm = f"{_n(min(alt))} / {_n(max(alt))} m" if alt else "n/a"
    L += [
        f"Distance: {_km(a.distance_m)} km (measured)",
        (
            f"Moving time: {_hms(a.moving_s)}; elapsed time: {_hms(a.elapsed_s)}; "
            f"stopped time: {stopped}"
        ),
        f"Average pace (moving): {_pace(avg_pace)} (calc); average speed: {speed}",
        (
            f"Elevation gain / loss: {_n(a.elev_gain_m, 0, ' m')} / {_n(a.elev_loss_m, 0, ' m')}; "
            f"altitude min / max: {alt_mm}"
        ),
        (
            f"Calories: {_n(a.calories_kcal, 0, ' kcal')} (est.); "
            f"average power: {_n(a.avg_power_w, 0, ' W')} (est.)"
        ),
    ]

    sec("HEART RATE")

    def pct(x: N) -> str:
        return _n(x / hr_max * 100, 0, "%") if x is not None and hr_max else "n/a"

    hs = sorted(hr)
    L += [
        (
            f"Average: {_n(a.avg_hr, 0, ' bpm')} (measured); max: {_n(a.max_hr, 0, ' bpm')} "
            f"(measured); athlete max HR setting: {_n(hr_max, 0, ' bpm')}"
        ),
        f"Average as % of max HR setting: {pct(a.avg_hr)}; peak as %: {pct(a.max_hr)}",
        f"Range held (per-second stream distribution, bpm): {_summary(hr)}",
        "Typical band (p5-p95): " + (f"{_n(_q(hs, 0.05))}-{_n(_q(hs, 0.95))} bpm" if hs else "n/a"),
    ]
    dur = (time[-1] or t0) - t0 if time else 0
    if dur > 600 and hr:
        h1, h2 = _avg(seg("hr", 0, dur / 2)), _avg(seg("hr", dur / 2))
        s1, s2 = _avg(seg("speed", 0, dur / 2)), _avg(seg("speed", dur / 2))
        drift = _n((h2 - h1) / h1 * 100, 1, "%") if h1 and h2 else "n/a"
        L.append(
            f"First half avg HR {_n(h1, 1)} bpm vs second half {_n(h2, 1)} bpm (drift {drift}); "
            f"first half pace {_pace(1000 / s1 if s1 else None)} vs second half "
            f"{_pace(1000 / s2 if s2 else None)}"
        )
    zs: list[float] = (m.time_in_zones_s if m else None) or []
    edges = cfg["hr_zones"]
    if zs and sum(zs) > 0:
        tot = sum(zs)
        L.append("Time in HR zones (zone: bpm range, time, share):")
        for i, z in enumerate(zs):
            hi = edges[i] if i < 4 else (hr_max if hr_max is not None else "max")
            rng = f"{edges[i - 1] if i else 0}-{hi}" if edges and len(edges) == 4 else "n/a"
            L.append(f"  Z{i + 1}: {rng} bpm, {_hms(z)}, {round(z / tot * 100)}%")

    sec("CADENCE")
    L.append(
        f"Average: {_n(a.avg_cadence_spm, 0, ' spm')}; per-second distribution: {_summary(cad)}"
    )

    sec("EFFICIENCY & LOAD")

    def mv(k: str) -> Any:
        return getattr(m, k) if m else None

    L += [
        (
            f"Efficiency factor: {_n(mv('efficiency_factor'), 2)} (calc, speed per bpm; "
            "higher = fitter)"
        ),
        (
            f"Aerobic decoupling (Pa:HR): {_n(mv('decoupling_pct'), 1, '%')} (calc); "
            f"pace variability CV: {_n(mv('pace_cv'), 3)} (calc); "
            f"steady run: {_yn(mv('is_steady'))}"
        ),
        f"TRIMP: {_n(mv('trimp'), 0)} (model); GPS flagged suspect: {_yn(mv('gps_suspect'))}",
    ]

    def first(*xs: N) -> N:
        return next((x for x in xs if x is not None), None)

    # per-km table: window HR from the stream, falling back to the split's own averages
    def table(title: str, laps: list[LapOut]) -> None:
        if not laps:
            return
        sec(title)
        L.append(
            "idx | dist_km | time | pace | avg_hr | hr_min-max | hr_p5-p95 | max_hr | cadence "
            "| elev_gain_m | pace_vs_run_avg_s"
        )
        cum: float = 0
        paces = [p for p in map(_lap_pace, laps) if p is not None]
        for i, lp in enumerate(laps, 1):
            frm = first(lp.start_offset_s, cum) or 0
            to = cum = frm + (first(lp.elapsed_s, lp.moving_s) or 0)
            h = sorted(seg("hr", frm, to))
            p = _lap_pace(lp)
            row: list[Any] = [
                i,
                _km(lp.distance_m),
                _hms(first(lp.moving_s, lp.elapsed_s)),
                _pace(p),
                _n(first(lp.avg_hr, _avg(h))),
                f"{_n(h[0])}-{_n(h[-1])}" if h else "n/a",
                f"{_n(_q(h, 0.05))}-{_n(_q(h, 0.95))}" if h else "n/a",
                _n(first(lp.max_hr, h[-1] if h else None)),
                _n(first(lp.avg_cadence_spm, _avg(seg("cadence", frm, to)))),
                _n(lp.elev_gain_m),
                f"{round(p - avg_pace):+d}" if p is not None and avg_pace is not None else "n/a",
            ]
            L.append(" | ".join(map(str, row)))
        if len(paces) > 1:
            fi, si = paces.index(min(paces)), paces.index(max(paces))
            half = len(paces) // 2
            p1, p2 = _avg(paces[:half]), _avg(paces[len(paces) - half :])
            kind = "n/a" if not (p1 and p2) else "negative split" if p2 < p1 else "positive split"
            L.append(
                f"Fastest: #{fi + 1} {_pace(paces[fi])}; slowest: #{si + 1} {_pace(paces[si])}; "
                f"first-half avg {_pace(p1)} vs second-half avg {_pace(p2)} ({kind})"
            )

    table("KM SPLITS (last split may be shorter than 1 km)", d.splits)
    if d.laps and len(d.laps) != len(d.splits):
        table("DEVICE LAPS", d.laps)
    return "\n".join(L)


@router.get("/activities/{aid}/report", response_class=PlainTextResponse)
def get_report(aid: int, s: Db) -> str:
    return build_report(s, aid)
