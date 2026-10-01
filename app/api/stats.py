"""Stats & records API (PLAN §11, §13, M3-04/05). SI units; weeks are ISO (Mon) on `local_date`.

Weighted pace is always total moving time / total distance, never a mean of paces.
"""

import calendar
import math
import statistics
from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import ColumnElement, func, or_, select

from app.api.activities import PACE, Db, SportType
from app.domain.models import Activity as A
from app.domain.models import ActivityMetrics, BestEffort, Setting
from app.metrics.engine import BEST_EFFORT_TARGETS

router = APIRouter(prefix="/api")

Bucket = Literal["week", "month"]
ROLLING_DAYS = 28
MIN_TREND_N = 8  # PLAN §13.4: no trend below this
MAX_THEIL_SEN_N = 500  # ponytail: O(n²) pairs; slope uses the latest 500 points, use binning beyond
SUMMARY_DEFAULT_DAYS = 28
ZONES = 5
DIST_LABELS = ("400m", "1K", "1mi", "5K", "10K", "Half Marathon", "Marathon")
LABELS = dict(zip(BEST_EFFORT_TARGETS, DIST_LABELS, strict=True))
# Histogram inner edges: class i is [edge[i-1], edge[i]); PLAN D9 for distance
HIST: dict[str, tuple[Any, str, tuple[float, ...]]] = {
    "distance": (A.distance_m, "m", (5000, 8000, 12000, 16000, 21000)),
    "duration": (A.moving_s, "s", (1800, 2700, 3600, 5400, 7200)),
    "pace": (PACE, "s/km", (240, 270, 300, 330, 360, 390, 420)),
}
TREND_VALUE: dict[str, tuple[Any, str]] = {
    "pace": (PACE, "s/km"),
    "ef": (ActivityMetrics.efficiency_factor, "(m/min)/bpm"),
    "hr": (A.avg_hr, "bpm"),
    "cadence": (A.avg_cadence_spm, "spm"),
}
AGG = (
    func.count(),
    func.sum(A.distance_m),
    func.sum(A.moving_s),
    func.sum(A.elev_gain_m),
    func.max(A.distance_m),
)


def _today() -> date:
    # ponytail: UTC date, up to a few hours off the runner's local day; add a tz setting if it matters
    return datetime.now(UTC).date()


# --- scope -------------------------------------------------------------------------
@dataclass(frozen=True)
class Scope:
    from_date: date | None
    to_date: date | None
    types: Sequence[str] | None
    include_indoor: bool

    def where(self) -> list[ColumnElement[bool]]:
        w: list[ColumnElement[bool]] = [
            A.excluded_from_stats.is_(False),
            A.duplicate_of_id.is_(None),
        ]
        if self.from_date:
            w.append(A.local_date >= self.from_date.isoformat())
        if self.to_date:
            w.append(A.local_date <= self.to_date.isoformat())
        if self.types:
            w.append(A.sport_type.in_(self.types))
        if not self.include_indoor:
            w.append(A.is_indoor.is_not(True))
        return w


def _scope(
    from_date: date | None = None,
    to_date: date | None = None,
    types: Annotated[list[SportType] | None, Query(alias="types[]")] = None,
    include_indoor: bool = False,
) -> Scope:
    if from_date and to_date and from_date > to_date:
        raise HTTPException(422, "from_date is after to_date")
    return Scope(from_date, to_date, types, include_indoor)


Sc = Annotated[Scope, Depends(_scope)]


# --- schemas -----------------------------------------------------------------------
class Totals(BaseModel):
    run_count: int
    distance_m: float
    moving_s: int
    elev_gain_m: float
    longest_run_m: float
    weighted_pace_s_per_km: float | None


def _tot(n: int, dist: Any, moving: Any, elev: Any, longest: Any) -> dict[str, Any]:
    d, m = float(dist or 0), int(moving or 0)
    return {
        "run_count": n,
        "distance_m": d,
        "moving_s": m,
        "elev_gain_m": float(elev or 0),
        "longest_run_m": float(longest or 0),
        "weighted_pace_s_per_km": m / (d / 1000) if d > 0 else None,
    }


class Period(BaseModel):
    from_date: date
    to_date: date
    days: int


class PeriodTotals(Totals):
    avg_weekly_distance_m: float


class Summary(BaseModel):
    period: Period
    previous_period: Period
    current: PeriodTotals
    previous: PeriodTotals


class BucketTotals(Totals):
    start: date
    end: date
    partial: bool


class Volume(BaseModel):
    bucket: Bucket
    items: list[BucketTotals]


class CalendarDay(BaseModel):
    date: date
    run_count: int
    distance_m: float


class CalendarOut(BaseModel):
    year: int
    days: list[CalendarDay]


class HistBucket(BaseModel):
    lo: float
    hi: float | None  # None = open-ended
    count: int


class Distribution(BaseModel):
    field: str
    unit: str
    n: int
    buckets: list[HistBucket]


class ZoneBucket(BaseModel):
    start: date
    end: date
    partial: bool
    zones_s: list[float]


class Zones(BaseModel):
    bucket: Bucket
    totals_s: list[float]
    items: list[ZoneBucket]


class TrendPoint(BaseModel):
    activity_id: int
    date: date
    value: float
    rolling_median: float


class TrendFit(BaseModel):
    slope_per_day: float  # Theil-Sen, in metric unit per day
    span_days: int


class Trends(BaseModel):
    metric: str
    unit: str
    n: int
    points: list[TrendPoint]
    trend: TrendFit | None  # None when n < MIN_TREND_N


class PaceHrPoint(BaseModel):
    activity_id: int
    date: date
    pace_s_per_km: float
    avg_hr: float
    moving_s: int


class PaceHr(BaseModel):
    n: int
    points: list[PaceHrPoint]


class TopWeek(Totals):
    start: date
    end: date


class Effort(BaseModel):
    activity_id: int
    local_date: str
    elapsed_s: int
    workout_type: str | None


class Record(BaseModel):
    distance_m: float
    label: str
    best: Effort | None
    best_race: Effort | None  # a training effort is not a race (PLAN D10)
    progression: list[Effort]  # each strictly faster than all earlier ones


# --- bucketing ---------------------------------------------------------------------
def _bucket_col(b: Bucket) -> ColumnElement[Any]:
    if b == "week":  # 'weekday 0' = next Sunday (or same day) -> minus 6 days = Monday
        return func.date(A.local_date, "weekday 0", "-6 days")
    return func.date(A.local_date, "start of month")


def _end(start: date, b: Bucket) -> date:
    if b == "week":
        return start + timedelta(days=6)
    return start.replace(day=calendar.monthrange(start.year, start.month)[1])


def _timeline(found: set[date], b: Bucket, sc: Scope) -> list[tuple[date, date, bool]]:
    """(start, end, partial) for every bucket from the scope start to min(scope end, today).

    Gaps are included so the client can draw zero weeks. A bucket is partial when the scope cuts
    it or it has not finished yet.
    """
    last_day = min(sc.to_date or _today(), _today())
    done = last_day - timedelta(days=1) if last_day == _today() else last_day  # today is still open
    first_day = sc.from_date or (min(found) if found else None)
    if first_day is None:
        return []
    starts = set(found)
    cur = (
        first_day - timedelta(days=first_day.weekday()) if b == "week" else first_day.replace(day=1)
    )
    while cur <= max([last_day, *found]):
        starts.add(cur)
        cur = _end(cur, b) + timedelta(days=1)
    return [
        (
            st,
            _end(st, b),
            (sc.from_date is not None and st < sc.from_date) or _end(st, b) > done,
        )
        for st in sorted(starts)
    ]


# --- endpoints ---------------------------------------------------------------------
@router.get("/stats/summary")
def summary(s: Db, sc: Sc) -> Summary:
    """Current period vs the immediately preceding one of the same length.

    The current end is clamped to today, so a partial period is compared over equal elapsed days.
    Default period: the last 28 days.
    """
    to = min(sc.to_date or _today(), _today())
    frm = sc.from_date or to - timedelta(days=SUMMARY_DEFAULT_DAYS - 1)
    if frm > to:
        raise HTTPException(422, "period is in the future")
    days = (to - frm).days + 1
    prev_to = frm - timedelta(days=1)
    prev_from = prev_to - timedelta(days=days - 1)

    def one(a: date, b: date) -> tuple[Period, PeriodTotals]:
        t = _tot(*s.execute(select(*AGG).where(*replace(sc, from_date=a, to_date=b).where())).one())
        weekly = t["distance_m"] / (days / 7)
        return Period(from_date=a, to_date=b, days=days), PeriodTotals(
            **t, avg_weekly_distance_m=weekly
        )

    (p, cur), (pp, prev) = one(frm, to), one(prev_from, prev_to)
    return Summary(period=p, previous_period=pp, current=cur, previous=prev)


@router.get("/stats/volume")
def volume(s: Db, sc: Sc, bucket: Bucket = "week") -> Volume:
    bk = _bucket_col(bucket).label("b")
    rows = {
        date.fromisoformat(b): row
        for b, *row in s.execute(select(bk, *AGG).where(*sc.where()).group_by(bk))
    }
    empty = (0, 0, 0, 0, 0)
    return Volume(
        bucket=bucket,
        items=[
            BucketTotals(start=st, end=en, partial=part, **_tot(*rows.get(st, empty)))
            for st, en, part in _timeline(set(rows), bucket, sc)
        ],
    )


@router.get("/stats/calendar")
def calendar_heatmap(
    s: Db, sc: Sc, year: Annotated[int | None, Query(ge=1970, le=2100)] = None
) -> CalendarOut:
    y = year or _today().year
    w = replace(sc, from_date=date(y, 1, 1), to_date=date(y, 12, 31)).where()
    rows = s.execute(
        select(A.local_date, func.count(), func.sum(A.distance_m))
        .where(*w)
        .group_by(A.local_date)
        .order_by(A.local_date)
    )
    return CalendarOut(
        year=y,
        days=[
            CalendarDay(date=date.fromisoformat(d), run_count=n, distance_m=float(dist or 0))
            for d, n, dist in rows
        ],
    )


@router.get("/stats/distribution")
def distribution(s: Db, sc: Sc, field: Literal["distance", "duration", "pace"]) -> Distribution:
    col, unit, edges = HIST[field]
    values: Sequence[float] = s.scalars(select(col).where(*sc.where(), col.is_not(None))).all()
    counts = [0] * (len(edges) + 1)
    for v in values:
        counts[bisect_right(edges, v)] += 1  # [lo, hi): a value on an edge goes to the upper class
    return Distribution(
        field=field,
        unit=unit,
        n=len(values),
        buckets=[
            HistBucket(
                lo=0 if i == 0 else edges[i - 1],
                hi=edges[i] if i < len(edges) else None,
                count=c,
            )
            for i, c in enumerate(counts)
        ],
    )


@router.get("/stats/zones")
def zones(s: Db, sc: Sc, bucket: Bucket = "week") -> Zones:
    bk = _bucket_col(bucket).label("b")
    z = [
        func.sum(func.json_extract(ActivityMetrics.time_in_zones_s, f"$[{i}]"))
        for i in range(ZONES)
    ]
    rows = {
        date.fromisoformat(b): [float(x or 0) for x in zs]
        for b, *zs in s.execute(
            select(bk, *z)
            .select_from(A)
            .join(ActivityMetrics, ActivityMetrics.activity_id == A.id)
            .where(
                *sc.where(),
                ActivityMetrics.time_in_zones_s.is_not(None),
                or_(A.workout_type.is_(None), A.workout_type != "race"),  # races excluded
            )
            .group_by(bk)
        )
    }
    zero = [0.0] * ZONES
    return Zones(
        bucket=bucket,
        totals_s=[sum(v[i] for v in rows.values()) for i in range(ZONES)],
        items=[
            ZoneBucket(start=st, end=en, partial=part, zones_s=rows.get(st, zero))
            for st, en, part in _timeline(set(rows), bucket, sc)
        ],
    )


def _steady(s: Db, sc: Scope, *cols: Any) -> list[Any]:
    """Steady runs (oldest first) as (local_date, id, *cols); NULL `cols` rows are dropped."""
    return list(
        s.execute(
            select(A.local_date, A.id, *cols)
            .select_from(A)
            .join(ActivityMetrics, ActivityMetrics.activity_id == A.id)
            .where(
                *sc.where(), ActivityMetrics.is_steady.is_(True), *(c.is_not(None) for c in cols)
            )
            .order_by(A.local_date, A.id)
        )
    )


def theil_sen(xs: list[float], ys: list[float]) -> float | None:
    """Median of pairwise slopes; pairs on the same x are skipped."""
    slopes = [
        (ys[j] - ys[i]) / (xs[j] - xs[i])
        for i in range(len(xs))
        for j in range(i + 1, len(xs))
        if xs[j] != xs[i]
    ]
    return statistics.median(slopes) if slopes else None


@router.get("/stats/trends")
def trends(s: Db, sc: Sc, metric: Literal["pace", "ef", "hr", "cadence"]) -> Trends:
    """Steady runs only (PLAN §13.4): points, trailing 28-day median and Theil-Sen slope."""
    col, unit = TREND_VALUE[metric]
    rows = _steady(s, sc, col)
    days = [date.fromisoformat(d).toordinal() for d, _, _ in rows]
    vals = [float(v) for _, _, v in rows]
    points = []
    for i, (d, aid, _) in enumerate(rows):
        lo = bisect_left(days, days[i] - ROLLING_DAYS + 1)
        hi = bisect_right(days, days[i])
        points.append(
            TrendPoint(
                activity_id=aid,
                date=date.fromisoformat(d),
                value=vals[i],
                rolling_median=statistics.median(vals[lo:hi]),
            )
        )
    fit = None
    if len(rows) >= MIN_TREND_N:
        xs, ys = days[-MAX_THEIL_SEN_N:], vals[-MAX_THEIL_SEN_N:]
        if (slope := theil_sen([float(x) for x in xs], ys)) is not None:
            fit = TrendFit(slope_per_day=slope, span_days=xs[-1] - xs[0])
    return Trends(metric=metric, unit=unit, n=len(rows), points=points, trend=fit)


@router.get("/stats/pace-hr")
def pace_hr(s: Db, sc: Sc) -> PaceHr:
    rows = _steady(s, sc, PACE, A.avg_hr, A.moving_s)
    pts = [
        PaceHrPoint(
            activity_id=aid, date=date.fromisoformat(d), pace_s_per_km=p, avg_hr=hr, moving_s=mv
        )
        for d, aid, p, hr, mv in rows
    ]
    return PaceHr(n=len(pts), points=pts)


@router.get("/stats/top-weeks")
def top_weeks(s: Db, sc: Sc, limit: Annotated[int, Query(ge=1, le=100)] = 10) -> list[TopWeek]:
    bk = _bucket_col("week").label("b")
    dist = func.coalesce(func.sum(A.distance_m), 0)
    rows = s.execute(
        select(bk, *AGG)
        .where(*sc.where())
        .group_by(bk)
        .order_by(dist.desc(), bk.desc())
        .limit(limit)
    )
    return [
        TopWeek(start=(st := date.fromisoformat(b)), end=_end(st, "week"), **_tot(*row))
        for b, *row in rows
    ]


class Vo2(BaseModel):
    vdot: float | None  # Daniels-Gilbert from the best recent effort
    vdot_source: str | None
    vdot_date: str | None
    hr_based: float | None  # Swain %HRR method on steady runs, needs hr_max + hr_rest settings
    hr_based_n: int


class Prediction(BaseModel):
    label: str
    seconds: int


class Fitness(BaseModel):
    vo2max: Vo2
    predictions: list[Prediction]  # Riegel from the VDOT effort
    ctl: float | None  # Banister EWMA of daily Edwards TRIMP, tau 42 d
    atl: float | None  # tau 7 d
    tsb: float | None  # ctl - atl
    acwr: float | None  # 7 d load / (28 d load / 4)
    monotony: float | None  # Foster: mean/sd of 7 daily loads
    strain: float | None  # weekly load * monotony


VDOT_WINDOW_DAYS = 180
PRED_TARGETS = {5000.0: "5K", 10000.0: "10K", 21097.5: "Half Marathon", 42195.0: "Marathon"}
RIEGEL_K = 1.06


def vdot(dist_m: float, secs: float) -> float:
    """Daniels-Gilbert: VO2 demanded by the speed / fraction of VO2max sustainable for the time."""
    t, v = secs / 60, dist_m / (secs / 60)
    vo2 = -4.60 + 0.182258 * v + 0.000104 * v * v
    frac = 0.8 + 0.1894393 * math.exp(-0.012778 * t) + 0.2989558 * math.exp(-0.1932605 * t)
    return vo2 / frac


def _load_indices(daily: dict[date, float], today: date) -> dict[str, float | None]:
    if not daily:
        return dict.fromkeys(("ctl", "atl", "tsb", "acwr", "monotony", "strain"))
    ctl = atl = 0.0
    day = min(daily)
    while day <= today:
        x = daily.get(day, 0.0)
        ctl += (x - ctl) * (1 - math.exp(-1 / 42))
        atl += (x - atl) * (1 - math.exp(-1 / 7))
        day += timedelta(days=1)
    last = [daily.get(today - timedelta(days=i), 0.0) for i in range(28)]
    week, sd = last[:7], statistics.pstdev(last[:7])
    acute, chronic = sum(week), sum(last) / 4
    mono = statistics.mean(week) / sd if sd else None
    return {
        "ctl": ctl,
        "atl": atl,
        "tsb": ctl - atl,
        "acwr": acute / chronic if chronic else None,
        "monotony": mono,
        "strain": acute * mono if mono else None,
    }


@router.get("/stats/fitness")
def fitness(s: Db, sc: Sc) -> Fitness:
    """Current-state indices, independent of the dashboard period (windows end today)."""
    today = _today()
    base = replace(sc, from_date=None, to_date=None)
    since = today - timedelta(days=VDOT_WINDOW_DAYS)
    best: tuple[float, float, int, str] | None = None  # vdot, dist, secs, date
    for dist, secs, day in s.execute(
        select(BestEffort.distance_m, BestEffort.elapsed_s, A.local_date)
        .select_from(BestEffort)
        .join(A, A.id == BestEffort.activity_id)
        .outerjoin(ActivityMetrics, ActivityMetrics.activity_id == A.id)
        .where(
            *replace(base, from_date=since).where(),
            ActivityMetrics.gps_suspect.is_not(True),
            BestEffort.distance_m.in_([d for d in LABELS if d >= 1000]),  # 400m is anaerobic
        )
    ):
        v = vdot(dist, secs)
        if best is None or v > best[0]:
            best = (v, dist, secs, day)

    hr: dict[str, Any] = dict(
        s.execute(
            select(Setting.key, Setting.value).where(Setting.key.in_(("hr_max", "hr_rest")))
        ).all()
    )
    est: list[float] = []
    if hr.get("hr_max") and hr.get("hr_rest"):
        span = hr["hr_max"] - hr["hr_rest"]
        for d_m, mv, avg in s.execute(
            select(A.distance_m, A.moving_s, A.avg_hr)
            .select_from(A)
            .join(ActivityMetrics, ActivityMetrics.activity_id == A.id)
            .where(
                *replace(base, from_date=today - timedelta(days=ROLLING_DAYS)).where(),
                ActivityMetrics.is_steady.is_(True),
                A.avg_hr.is_not(None),
                A.moving_s > 0,
                A.distance_m.is_not(None),
            )
        ):
            frac = (avg - hr["hr_rest"]) / span
            if 0.3 < frac <= 1:  # outside this the %HRR-%VO2R line is meaningless
                v = float(d_m or 0) / (float(mv or 1) / 60)
                est.append(0.2 * v / frac + 3.5)  # ACSM running VO2, Swain

    daily: dict[date, float] = defaultdict(float)
    for d, zs, mv in s.execute(
        select(A.local_date, ActivityMetrics.time_in_zones_s, A.moving_s)
        .select_from(A)
        .join(ActivityMetrics, ActivityMetrics.activity_id == A.id)
        .where(*replace(base, from_date=today - timedelta(days=VDOT_WINDOW_DAYS)).where())
    ):
        # Edwards TRIMP: minutes in zone k weighted k; no HR -> assume Z2 (ponytail: crude)
        load = sum((k + 1) * t / 60 for k, t in enumerate(zs)) if zs else 2 * (mv or 0) / 60
        daily[date.fromisoformat(d)] += load

    preds = []
    if best:
        _, d1, t1, _ = best
        preds = [
            Prediction(label=lb, seconds=round(t1 * (d2 / d1) ** RIEGEL_K))
            for d2, lb in PRED_TARGETS.items()
        ]
    return Fitness(
        vo2max=Vo2(
            vdot=best[0] if best else None,
            vdot_source=LABELS[best[1]] if best else None,
            vdot_date=best[3] if best else None,
            hr_based=statistics.median(est) if est else None,
            hr_based_n=len(est),
        ),
        predictions=preds,
        **_load_indices(daily, today),
    )


@router.get("/records")
def records(s: Db) -> list[Record]:
    """All-time PRs per standard distance (no indoor, no gps_suspect) plus how they progressed."""
    progression: dict[float, list[Effort]] = {d: [] for d in LABELS}
    race: dict[float, Effort] = {}
    rows = s.execute(
        select(BestEffort.distance_m, BestEffort.elapsed_s, A.id, A.local_date, A.workout_type)
        .select_from(BestEffort)
        .join(A, A.id == BestEffort.activity_id)
        .outerjoin(ActivityMetrics, ActivityMetrics.activity_id == A.id)
        .where(
            A.excluded_from_stats.is_(False),
            A.duplicate_of_id.is_(None),
            A.is_indoor.is_not(True),
            ActivityMetrics.gps_suspect.is_not(True),
            BestEffort.distance_m.in_(list(LABELS)),
        )
        .order_by(A.start_time_utc, A.id)
    )
    for dist, secs, aid, day, wtype in rows:
        e = Effort(activity_id=aid, local_date=day, elapsed_s=secs, workout_type=wtype)
        prog = progression[dist]
        if not prog or secs < prog[-1].elapsed_s:
            prog.append(e)
        if wtype == "race" and (dist not in race or secs < race[dist].elapsed_s):
            race[dist] = e
    return [
        Record(
            distance_m=d,
            label=label,
            best=progression[d][-1] if progression[d] else None,
            best_race=race.get(d),
            progression=progression[d],
        )
        for d, label in LABELS.items()
    ]
