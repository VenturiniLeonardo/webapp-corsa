"""Activities read/edit API (PLAN §11, M3-01..03). SI units only; pace is derived, never stored."""

import gzip
import json
from collections.abc import Sequence
from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy import ColumnElement, SQLColumnExpression, delete, func, or_, select
from sqlalchemy.orm import Session

from app.core.db import get_session
from app.domain.models import (
    Activity,
    ActivityMetrics,
    ActivityTag,
    BestEffort,
    Lap,
    SourceRecord,
    Stream,
    Tag,
)
from app.domain.stream_codec import decode_stream

router = APIRouter(prefix="/api")
Db = Annotated[Session, Depends(get_session)]

SportType = Literal["run", "trail_run", "treadmill"]
WorkoutType = Literal["easy", "long", "workout", "race", "other"]
SourceName = Literal["strava", "file_fit", "file_gpx", "file_tcx", "apple_health"]
CHANNELS = ("time", "distance", "hr", "speed", "lat", "lng", "altitude", "cadence", "power")
SIMILAR_TOL = 0.15
SIMILAR_N = 5

A = Activity
PACE = A.moving_s * 1000.0 / func.nullif(A.distance_m, 0)  # s/km, NULL when distance is 0/NULL
SORTS: dict[str, SQLColumnExpression[Any]] = {
    "date": A.start_time_utc,
    "name": A.name,
    "type": A.sport_type,
    "distance": A.distance_m,
    "duration": A.moving_s,
    "pace": PACE,
    "hr": A.avg_hr,
    "elev": A.elev_gain_m,
}


# --- schemas -----------------------------------------------------------------
class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ActivitySummary(_Out):
    id: int
    name: str | None
    sport_type: str
    workout_type: str | None
    start_time_utc: str
    timezone: str | None
    local_date: str
    distance_m: float | None
    moving_s: int | None
    elapsed_s: int | None
    elev_gain_m: float | None
    avg_hr: float | None
    excluded_from_stats: bool
    duplicate_of_id: int | None
    tags: list[str] = []


class ActivityOut(ActivitySummary):
    notes: str | None
    elev_loss_m: float | None
    max_hr: float | None
    avg_cadence_spm: float | None
    avg_power_w: float | None
    calories_kcal: int | None
    has_gps: bool | None
    has_hr: bool | None
    has_cadence: bool | None
    is_indoor: bool | None
    primary_source_id: int | None
    stream_source_id: int | None
    summary_polyline: str | None
    upstream_deleted_at: str | None
    created_at: str
    updated_at: str


class Aggregate(BaseModel):
    count: int
    distance_m: float
    moving_s: int
    weighted_pace_s_per_km: float | None


class ActivityList(BaseModel):
    items: list[ActivitySummary]
    total: int
    aggregate: Aggregate


class MetricsOut(_Out):
    algo_version: int
    computed_at: str
    zones_hash: str | None
    time_in_zones_s: Any
    efficiency_factor: float | None
    pace_cv: float | None
    is_steady: bool | None
    decoupling_pct: float | None
    trimp: float | None
    gps_suspect: bool | None


class LapOut(_Out):
    idx: int
    start_offset_s: int | None
    elapsed_s: int | None
    moving_s: int | None
    distance_m: float | None
    avg_speed_ms: float | None
    avg_hr: float | None
    max_hr: float | None
    avg_cadence_spm: float | None
    elev_gain_m: float | None


class BestEffortOut(_Out):
    distance_m: float
    elapsed_s: int
    start_offset_s: int | None
    is_pr: bool = False


class SourceOut(_Out):
    id: int
    source: str
    external_id: str
    status: str
    fetched_at: str | None
    mapper_version: int | None
    is_primary: bool = False


class ActivityDetail(BaseModel):
    activity: ActivityOut
    metrics: MetricsOut | None
    laps: list[LapOut]
    splits: list[LapOut]
    best_efforts: list[BestEffortOut]
    sources: list[SourceOut]
    tags: list[str]
    duplicate_candidates: list[ActivitySummary]


class SimilarRun(ActivitySummary):
    """Deltas are this activity minus the similar one (positive = this run is slower/higher)."""

    pace_s_per_km: float | None = None
    efficiency_factor: float | None = None
    pace_delta_s_per_km: float | None = None
    hr_delta_bpm: float | None = None
    ef_delta: float | None = None


class TagCount(BaseModel):
    name: str
    count: int


class ActivityPatch(BaseModel):
    """Only fields present in the body are applied; anything else is a 422."""

    model_config = ConfigDict(extra="forbid")
    notes: str | None = None
    workout_type: WorkoutType | None = None
    tags: list[
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
    ] = []
    excluded_from_stats: bool = False


# --- helpers -----------------------------------------------------------------
def _get(s: Session, aid: int) -> Activity:
    act = s.get(Activity, aid)
    if act is None:
        raise HTTPException(404, "activity not found")
    return act


def _out[M: ActivitySummary](s: Session, cls: type[M], acts: Sequence[Activity]) -> list[M]:
    """Validate ORM rows into `cls`, attaching tags with one query."""
    tags: dict[int, list[str]] = {}
    for aid, name in s.execute(
        select(ActivityTag.activity_id, Tag.name)
        .join(Tag, Tag.id == ActivityTag.tag_id)
        .where(ActivityTag.activity_id.in_([a.id for a in acts]))
        .order_by(Tag.name)
    ):
        tags.setdefault(aid, []).append(name)
    return [cls.model_validate(a).model_copy(update={"tags": tags.get(a.id, [])}) for a in acts]


def _pace(a: Activity) -> float | None:
    return a.moving_s / (a.distance_m / 1000) if a.moving_s and a.distance_m else None


def _delta(x: float | None, y: float | None) -> float | None:
    return None if x is None or y is None else x - y


def _set_tags(s: Session, aid: int, names: list[str]) -> None:
    uniq: dict[str, str] = {}
    for n in names:  # tags are case-insensitive unique (NOCASE); first spelling wins
        uniq.setdefault(n.lower(), n)
    have = {
        t.name.lower(): t for t in s.scalars(select(Tag).where(Tag.name.in_(list(uniq.values()))))
    }
    for k, n in uniq.items():
        if k not in have:
            have[k] = Tag(name=n)
            s.add(have[k])
    s.flush()
    s.execute(delete(ActivityTag).where(ActivityTag.activity_id == aid))
    s.add_all(ActivityTag(activity_id=aid, tag_id=have[k].id) for k in uniq)


# --- endpoints ---------------------------------------------------------------
@router.get("/activities")
def list_activities(
    s: Db,
    from_date: date | None = None,
    to_date: date | None = None,
    dist_min: float | None = None,
    dist_max: float | None = None,
    dur_min: float | None = None,
    dur_max: float | None = None,
    pace_min: float | None = None,
    pace_max: float | None = None,
    hr_min: float | None = None,
    hr_max: float | None = None,
    type_: Annotated[list[SportType] | None, Query(alias="type[]")] = None,
    workout_type: Annotated[list[WorkoutType] | None, Query(alias="workout_type[]")] = None,
    tag: Annotated[list[str] | None, Query(alias="tag[]")] = None,
    source: Annotated[list[SourceName] | None, Query(alias="source[]")] = None,
    q: str | None = None,
    include_excluded: bool = False,
    sort: Literal["date", "name", "type", "distance", "duration", "pace", "hr", "elev"] = "date",
    order: Literal["asc", "desc"] = "desc",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ActivityList:
    """Distances in m, durations in s, pace in s/km, HR in bpm; `tag[]` matches any of the tags."""
    w: list[ColumnElement[bool]] = []
    if not include_excluded:
        w += [A.excluded_from_stats.is_(False), A.duplicate_of_id.is_(None)]
    if from_date:
        w.append(A.local_date >= from_date.isoformat())
    if to_date:
        w.append(A.local_date <= to_date.isoformat())
    ranges: list[tuple[SQLColumnExpression[Any], float | None, float | None]] = [
        (A.distance_m, dist_min, dist_max),
        (A.moving_s, dur_min, dur_max),
        (PACE, pace_min, pace_max),
        (A.avg_hr, hr_min, hr_max),
    ]
    for col, lo, hi in ranges:
        if lo is not None:
            w.append(col >= lo)
        if hi is not None:
            w.append(col <= hi)
    if type_:
        w.append(A.sport_type.in_(type_))
    if workout_type:
        w.append(A.workout_type.in_(workout_type))
    if tag:
        w.append(
            A.id.in_(
                select(ActivityTag.activity_id)
                .join(Tag, Tag.id == ActivityTag.tag_id)
                .where(Tag.name.in_(tag))
            )
        )
    if source:
        w.append(A.id.in_(select(SourceRecord.activity_id).where(SourceRecord.source.in_(source))))
    if q:
        w.append(or_(A.name.icontains(q, autoescape=True), A.notes.icontains(q, autoescape=True)))

    n, dist, moving = s.execute(
        select(
            func.count(),
            func.coalesce(func.sum(A.distance_m), 0.0),
            func.coalesce(func.sum(A.moving_s), 0),
        ).where(*w)
    ).one()
    dist_m, moving_s = float(dist or 0), int(moving or 0)
    desc = order == "desc"
    key = SORTS[sort]
    rows = s.scalars(
        select(A)
        .where(*w)
        .order_by((key.desc() if desc else key.asc()).nulls_last(), A.id.desc() if desc else A.id)
        .limit(page_size)
        .offset((page - 1) * page_size)
    ).all()
    return ActivityList(
        items=_out(s, ActivitySummary, rows),
        total=n,
        aggregate=Aggregate(
            count=n,
            distance_m=dist_m,
            moving_s=moving_s,
            weighted_pace_s_per_km=moving_s / (dist_m / 1000) if dist_m > 0 else None,
        ),
    )


@router.get("/activities/{aid}")
def get_activity(aid: int, s: Db) -> ActivityDetail:
    act = _get(s, aid)
    laps = s.scalars(select(Lap).where(Lap.activity_id == aid).order_by(Lap.kind, Lap.idx)).all()
    efforts = s.scalars(
        select(BestEffort).where(BestEffort.activity_id == aid).order_by(BestEffort.distance_m)
    ).all()
    # PR at the time of the run: faster than every earlier counted effort at that distance
    counts = not act.excluded_from_stats and act.duplicate_of_id is None
    prev = {
        d: best
        for d, best in s.execute(
            select(BestEffort.distance_m, func.min(BestEffort.elapsed_s))
            .join(A, A.id == BestEffort.activity_id)
            .where(
                A.start_time_utc < act.start_time_utc,
                A.excluded_from_stats.is_(False),
                A.duplicate_of_id.is_(None),
            )
            .group_by(BestEffort.distance_m)
        )
    }
    best_efforts = [
        BestEffortOut.model_validate(b).model_copy(
            update={
                "is_pr": counts and (b.distance_m not in prev or b.elapsed_s < prev[b.distance_m])
            }
        )
        for b in efforts
    ]
    sources = [
        SourceOut.model_validate(r).model_copy(update={"is_primary": r.id == act.primary_source_id})
        for r in s.scalars(
            select(SourceRecord).where(SourceRecord.activity_id == aid).order_by(SourceRecord.id)
        )
    ]
    dups = s.scalars(
        select(A).where(or_(A.duplicate_of_id == aid, A.id == act.duplicate_of_id)).order_by(A.id)
    ).all()
    (full,) = _out(s, ActivityOut, [act])
    metrics = s.get(ActivityMetrics, aid)
    return ActivityDetail(
        activity=full,
        metrics=MetricsOut.model_validate(metrics) if metrics else None,
        laps=[LapOut.model_validate(x) for x in laps if x.kind == "device_lap"],
        splits=[LapOut.model_validate(x) for x in laps if x.kind == "split_km"],
        best_efforts=best_efforts,
        sources=sources,
        tags=full.tags,
        duplicate_candidates=_out(s, ActivitySummary, dups),
    )


@router.get("/activities/{aid}/streams")
def get_streams(aid: int, s: Db, channels: str | None = None) -> Response:
    """`?channels=time,hr,...` (default: all known channels); always gzip-encoded."""
    act = _get(s, aid)
    want = [c for c in channels.split(",") if c] if channels else list(CHANNELS)
    if bad := set(want) - set(CHANNELS):
        raise HTTPException(422, f"unknown channels: {sorted(bad)}")
    sid = act.stream_source_id or act.primary_source_id
    st = s.get(Stream, sid) if sid else None
    data = decode_stream(st.data) if st else {}
    body = json.dumps({"channels": {k: data[k] for k in want if k in data}}, separators=(",", ":"))
    return Response(
        gzip.compress(body.encode(), mtime=0),
        media_type="application/json",
        headers={"Content-Encoding": "gzip"},
    )


@router.get("/activities/{aid}/similar")
def get_similar(aid: int, s: Db) -> list[SimilarRun]:
    act = _get(s, aid)
    if not act.distance_m:
        return []
    rows = s.scalars(
        select(A)
        .where(
            A.id != aid,
            A.sport_type == act.sport_type,
            A.distance_m.between(
                act.distance_m * (1 - SIMILAR_TOL), act.distance_m * (1 + SIMILAR_TOL)
            ),
            A.excluded_from_stats.is_(False),
            A.duplicate_of_id.is_(None),
        )
        .order_by(A.start_time_utc.desc())
        .limit(SIMILAR_N)
    ).all()
    ef = {
        i: v
        for i, v in s.execute(
            select(ActivityMetrics.activity_id, ActivityMetrics.efficiency_factor).where(
                ActivityMetrics.activity_id.in_([aid, *(r.id for r in rows)])
            )
        )
    }
    out = _out(s, SimilarRun, rows)
    for r, o in zip(out, rows, strict=True):
        r.pace_s_per_km, r.efficiency_factor = _pace(o), ef.get(o.id)
        r.pace_delta_s_per_km = _delta(_pace(act), r.pace_s_per_km)
        r.hr_delta_bpm = _delta(act.avg_hr, o.avg_hr)
        r.ef_delta = _delta(ef.get(aid), r.efficiency_factor)
    return out


@router.patch("/activities/{aid}")
def patch_activity(aid: int, body: ActivityPatch, s: Db) -> ActivityOut:
    act = _get(s, aid)
    sent = body.model_fields_set
    for f in sent - {"tags"}:
        setattr(act, f, getattr(body, f))
    if "tags" in sent:
        _set_tags(s, aid, body.tags)
    s.commit()
    return _out(s, ActivityOut, [act])[0]


@router.get("/tags")
def list_tags(s: Db) -> list[TagCount]:
    return [
        TagCount(name=name, count=n)
        for name, n in s.execute(
            select(Tag.name, func.count())
            .join(ActivityTag, ActivityTag.tag_id == Tag.id)
            .group_by(Tag.id)
            .order_by(Tag.name)
        )
    ]
