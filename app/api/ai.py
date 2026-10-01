"""AI analysis endpoints (PLAN §26). GET never calls the model; POST is the only paid path.

Flow: deterministic context (reusing the stats/activity endpoints) -> sufficiency gate -> cache
by input hash -> OpenRouter (fallback chain) -> validated result stored -> returned.
Context sent to the model: numbers and dates only. No name, notes, tags, GPS, ids or times of day.
"""

import hashlib
import json
import threading
from datetime import date
from typing import Any, Literal, cast

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import Engine, delete, func, select

from app.ai.openrouter import AiError, OpenRouter, usage_today
from app.ai.prompts import PROMPT_VERSION, Analysis, build_messages
from app.api import stats
from app.api.activities import Db, get_activity, get_similar
from app.core.config import get_settings
from app.domain.models import Activity, AiAnalysis

router = APIRouter(prefix="/api/ai")
Kind = Literal["activity", "period"]
MIN_PERIOD_RUNS = 3
MAX_SPLITS = 50
MAX_SERIES = 26  # buckets sent to the model; longer periods switch to months
LOAD_SPIKE = 1.15  # PLAN D2: > 10-15 %/week jumps
LOAD_BASE_KM = 5.0  # ignore spikes from a near-zero base
_busy = threading.Lock()  # one generation at a time: a double click must not cost two requests


class Stored(BaseModel):
    model: str
    created_at: str
    analysis: Analysis


class AiState(BaseModel):
    enabled: bool
    used_today: int
    daily_limit: int
    insufficient: str | None  # reason the app refuses to ask the model
    stale: bool  # data or prompt changed since `result` was generated
    result: Stored | None


class PeriodIn(BaseModel):
    from_date: date | None = None
    to_date: date | None = None


def _err(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(status, {"code": code, "message": message})


# --- formatting helpers (the model gets ready-made numbers, never raw SI to convert) ---
def _r(x: float | None, nd: int = 1) -> float | None:
    return None if x is None else round(x, nd)


def _pace(sec_per_km: float | None) -> str | None:
    if not sec_per_km or sec_per_km <= 0:
        return None
    s = round(sec_per_km)
    return f"{s // 60}:{s % 60:02d}/km"


def _clean(d: Any) -> Any:
    """Drop None/empty values recursively: shorter prompt, and absent reads as unknown."""
    if isinstance(d, dict):
        return {k: v for k, v in ((k, _clean(v)) for k, v in d.items()) if v not in (None, [], {})}
    if isinstance(d, list):
        return [_clean(v) for v in d]
    return d


# --- context builders ------------------------------------------------------------------
def activity_context(s: Db, aid: int) -> tuple[dict[str, Any], str | None]:
    d = get_activity(aid, s)
    a, m = d.activity, d.metrics
    if not a.distance_m or not a.moving_s:
        return {}, "This run has no distance or moving time."
    zones = (m.time_in_zones_s if m else None) or []
    zt = sum(zones)
    ctx = {
        "date": a.local_date,
        "type": a.sport_type,
        "workout_type": a.workout_type,
        "indoor": a.is_indoor,
        "distance_km": _r(a.distance_m / 1000, 2),
        "moving_time_min": _r(a.moving_s / 60),
        "elapsed_time_min": _r((a.elapsed_s or 0) / 60) or None,
        "avg_pace": _pace(a.moving_s / (a.distance_m / 1000)),
        "elev_gain_m": _r(a.elev_gain_m, 0),
        "avg_hr_bpm": _r(a.avg_hr, 0),
        "max_hr_bpm": _r(a.max_hr, 0),
        "avg_cadence_spm": _r(a.avg_cadence_spm, 0),
        "avg_power_w_estimated": _r(a.avg_power_w, 0),
        "metrics": None
        if m is None
        else {
            "efficiency_factor": _r(m.efficiency_factor, 3),
            "pace_variability_cv": _r(m.pace_cv, 3),
            "steady_run": m.is_steady,
            "hr_decoupling_pct": _r(m.decoupling_pct),
            "gps_suspect": m.gps_suspect or None,
            "time_in_hr_zones_pct": [_r(100 * z / zt) for z in zones] if zt else None,
        },
        "km_splits": [
            {
                "km": sp.idx + 1,
                "dist_m": _r(sp.distance_m, 0) if sp.distance_m and sp.distance_m < 990 else None,
                "pace": _pace(1000 / sp.avg_speed_ms if sp.avg_speed_ms else None),
                "hr": _r(sp.avg_hr, 0),
                "elev_gain_m": _r(sp.elev_gain_m, 0),
            }
            for sp in d.splits[:MAX_SPLITS]
        ],
        "best_efforts": [
            {"distance_m": b.distance_m, "time_s": b.elapsed_s, "personal_record_then": b.is_pr}
            for b in d.best_efforts
        ],
        "similar_recent_runs": [
            {
                "date": r.local_date,
                "distance_km": _r((r.distance_m or 0) / 1000, 2),
                "pace": _pace(r.pace_s_per_km),
                "avg_hr_bpm": _r(r.avg_hr, 0),
                "efficiency_factor": _r(r.efficiency_factor, 3),
                "this_run_pace_delta_s_per_km": _r(r.pace_delta_s_per_km, 0),
                "this_run_hr_delta_bpm": _r(r.hr_delta_bpm, 0),
            }
            for r in get_similar(aid, s)
        ],
        "notes_on_units": "pace min:s per km; negative pace delta = this run was faster",
    }
    return cast(dict[str, Any], _clean(ctx)), None


def _period_bounds(s: Db, body: PeriodIn) -> tuple[date, date]:
    to = min(body.to_date or stats._today(), stats._today())
    frm = body.from_date
    if frm is None:
        first = s.scalar(select(func.min(Activity.local_date)))
        frm = date.fromisoformat(first) if first else to
    if frm > to:
        raise _err(422, "invalid_period", "The period starts after it ends.")
    return frm, to


def _trend(t: stats.Trends, unit: str) -> dict[str, Any]:
    out: dict[str, Any] = {"steady_runs_n": t.n}
    if t.trend and t.points:
        first, last = t.points[0].rolling_median, t.points[-1].rolling_median
        out["trend"] = {
            "rolling_28d_median_start": first,
            "rolling_28d_median_end": last,
            f"theil_sen_change_per_4_weeks_{unit}": _r(t.trend.slope_per_day * 28, 3),
            "span_days": t.trend.span_days,
        }
    return out


def period_context(s: Db, frm: date, to: date) -> tuple[dict[str, Any], str | None]:
    sc = stats.Scope(frm, to, None, False)
    summ = stats.summary(s, sc)
    cur, prev = summ.current, summ.previous
    if cur.run_count < MIN_PERIOD_RUNS:
        return {}, f"Fewer than {MIN_PERIOD_RUNS} runs in this period."

    def tot(t: stats.PeriodTotals) -> dict[str, Any]:
        return {
            "runs": t.run_count,
            "distance_km": _r(t.distance_m / 1000),
            "moving_time_h": _r(t.moving_s / 3600),
            "elev_gain_m": _r(t.elev_gain_m, 0),
            "longest_run_km": _r(t.longest_run_m / 1000),
            "avg_weekly_km": _r(t.avg_weekly_distance_m / 1000),
            "weighted_pace": _pace(t.weighted_pace_s_per_km),
        }

    weeks = stats.volume(s, sc, "week").items
    series = weeks if len(weeks) <= MAX_SERIES else stats.volume(s, sc, "month").items
    flags: list[str] = []
    done = [w for w in weeks if not w.partial]
    for i in range(4, len(done)):
        base = sum(w.distance_m for w in done[i - 4 : i]) / 4 / 1000
        km = done[i].distance_m / 1000
        if base >= LOAD_BASE_KM and km > base * LOAD_SPIKE:
            flags.append(f"week of {done[i].start}: {km:.1f} km vs {base:.1f} km 4-week avg")
    if empty := sum(1 for w in done if w.run_count == 0):
        flags.append(f"{empty} complete week(s) without runs")
    ztot = stats.zones(s, sc, "week").totals_s
    zsum = sum(ztot)
    mix = dict(
        s.execute(
            select(func.coalesce(Activity.workout_type, "untagged"), func.count())
            .where(*sc.where())
            .group_by(Activity.workout_type)
        ).all()
    )
    prs = [
        {"distance": r.label, "date": e.local_date, "time_s": e.elapsed_s}
        for r in stats.records(s)
        for e in r.progression
        if frm.isoformat() <= e.local_date <= to.isoformat()
    ]
    ctx = {
        "period": {"from": frm.isoformat(), "to": to.isoformat(), "days": summ.period.days},
        "current": tot(cur),
        "previous_equal_length": tot(prev),
        "volume_by_" + ("week" if series is weeks else "month"): [
            {"start": b.start.isoformat(), "km": _r(b.distance_m / 1000), "runs": b.run_count}
            | ({"partial": True} if b.partial else {})
            for b in series
        ],
        "flags": flags,
        "workout_type_counts": mix,
        "time_in_hr_zones_pct": [_r(100 * z / zsum) for z in ztot] if zsum else None,
        "pace_steady_runs": _trend(stats.trends(s, sc, "pace"), "s_per_km"),
        "efficiency_factor_steady_runs": _trend(stats.trends(s, sc, "ef"), "ef"),
        "personal_records_set": prs,
        "notes_on_units": (
            "trend needs >= 8 steady runs and is absent otherwise; negative pace change = faster; "
            "higher efficiency factor = more speed per heartbeat; outdoor runs only"
        ),
    }
    return cast(dict[str, Any], _clean(ctx)), None


# --- cache + generation ----------------------------------------------------------------
def _hash(subject: str, ctx: dict[str, Any]) -> str:
    key = {"v": PROMPT_VERSION, "models": get_settings().ai_models, "s": subject, "ctx": ctx}
    return hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()


def _state(s: Db, kind: Kind, subject: str, ctx: dict[str, Any], why: str | None) -> AiState:
    row = s.scalars(
        select(AiAnalysis)
        .where(AiAnalysis.kind == kind, AiAnalysis.subject == subject)
        .order_by(AiAnalysis.id.desc())
        .limit(1)
    ).first()
    cfg = get_settings()
    return AiState(
        enabled=bool(cfg.OPENROUTER_API_KEY),
        used_today=usage_today(cast(Engine, s.get_bind())),
        daily_limit=cfg.AI_DAILY_LIMIT,
        insufficient=why,
        stale=row is not None and row.input_hash != _hash(subject, ctx),
        result=None
        if row is None
        else Stored(model=row.model, created_at=row.created_at, analysis=Analysis(**row.result)),
    )


def _generate(s: Db, kind: Kind, subject: str, ctx: dict[str, Any], why: str | None) -> AiState:
    if why:
        raise _err(422, "insufficient_data", why)
    h = _hash(subject, ctx)
    if s.scalar(select(AiAnalysis.id).where(AiAnalysis.kind == kind, AiAnalysis.input_hash == h)):
        return _state(s, kind, subject, ctx, None)  # same data, same prompt: no request
    if not _busy.acquire(blocking=False):
        raise _err(409, "busy", "An AI analysis is already running.")
    try:
        model, analysis = OpenRouter(cast(Engine, s.get_bind())).analyse(build_messages(kind, ctx))
    except AiError as e:
        status = {"not_configured": 503, "rate_limited": 429}.get(e.code, 502)
        raise _err(status, e.code, e.message) from None
    finally:
        _busy.release()
    # one result per subject: the previous one is superseded
    s.execute(delete(AiAnalysis).where(AiAnalysis.kind == kind, AiAnalysis.subject == subject))
    s.add(
        AiAnalysis(
            kind=kind, subject=subject, input_hash=h, model=model, result=analysis.model_dump()
        )
    )
    s.commit()
    return _state(s, kind, subject, ctx, None)


# --- endpoints -------------------------------------------------------------------------
@router.get("/activity/{aid}")
def get_activity_analysis(aid: int, s: Db) -> AiState:
    return _state(s, "activity", str(aid), *activity_context(s, aid))


@router.post("/activity/{aid}")
def analyse_activity(aid: int, s: Db) -> AiState:
    return _generate(s, "activity", str(aid), *activity_context(s, aid))


@router.get("/period")
def get_period_analysis(
    s: Db, from_date: date | None = None, to_date: date | None = None
) -> AiState:
    frm, to = _period_bounds(s, PeriodIn(from_date=from_date, to_date=to_date))
    return _state(s, "period", f"{frm}..{to}", *period_context(s, frm, to))


@router.post("/period")
def analyse_period(body: PeriodIn, s: Db) -> AiState:
    frm, to = _period_bounds(s, body)
    return _generate(s, "period", f"{frm}..{to}", *period_context(s, frm, to))
