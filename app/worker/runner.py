"""Job handlers + shared store helpers (PLAN §8.1, §8.5). One DB transaction per activity."""

import hashlib
import json
import threading
from bisect import bisect_right
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session

from app.api.ai import queue_auto_analysis, run_sweep
from app.domain.dedup import (
    MAX_START_DELTA_S,
    ActivitySummary,
    DedupAction,
    find_duplicate_candidate,
)
from app.domain.models import (
    Activity,
    ActivityMetrics,
    BestEffort,
    Job,
    Lap,
    Setting,
    SourceRecord,
    Stream,
    utcnow_iso,
)
from app.domain.stream_codec import decode_stream, encode_stream
from app.ingest.strava_mapper import LapDraft, StreamDraft
from app.metrics import engine as metrics
from app.worker.queue import HEARTBEAT_INTERVAL_S, JobQueue

BACKOFF_S = (10, 60, 300)
CODEC_VERSION = 1
ACTIVITY_FIELDS = [
    "sport_type",
    "name",
    "start_time_utc",
    "timezone",
    "local_date",
    "elapsed_s",
    "moving_s",
    "distance_m",
    "elev_gain_m",
    "avg_hr",
    "max_hr",
    "avg_cadence_spm",
    "avg_power_w",
    "calories_kcal",
    "has_gps",
    "has_hr",
    "has_cadence",
    "is_indoor",
    "workout_type",
    "summary_polyline",
]
Cfg = tuple[list[float] | None, float, float]  # zones, steady CV threshold, ref pace s/km
DEFAULT_REF_PACE_S = 420.0  # 7:00/km


def _now_iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def _transient(e: Exception) -> bool:
    if isinstance(e, httpx.HTTPStatusError):
        return e.response.status_code >= 500
    return isinstance(e, httpx.TransportError)


def _cfg(s: Session) -> Cfg:
    v: dict[str, Any] = dict(
        s.execute(
            select(Setting.key, Setting.value).where(
                Setting.key.in_(("hr_zones", "steady_cv_threshold", "ref_pace_s_per_km"))
            )
        ).all()
    )
    return (
        v.get("hr_zones") or None,
        float(v.get("steady_cv_threshold") or 0.08),
        float(v.get("ref_pace_s_per_km") or DEFAULT_REF_PACE_S),
    )


def _record(s: Session, ext_id: str, source: str) -> SourceRecord:
    rec = s.scalar(
        select(SourceRecord).where(
            SourceRecord.source == source, SourceRecord.external_id == ext_id
        )
    )
    if rec is None:
        rec = SourceRecord(source=source, external_id=ext_id, status="pending")
        s.add(rec)
        s.flush()
    return rec


def adjusted_ef(act: Activity, m: ActivityMetrics) -> float | None:
    """EF on GAP (raw speed when there is no altitude) + heat; only where plain EF exists."""
    if m.efficiency_factor is None:
        return None
    speed = m.gap_speed_ms or float(act.distance_m or 0) / (act.moving_s or 1)
    return metrics.compute_adjusted_ef(
        speed, act.avg_hr, act.weather_temp_c, act.weather_dew_point_c
    )


def _compute_metrics(s: Session, act: Activity, ch: dict[str, list[Any]], cfg: Cfg) -> None:
    """Derived rows for one activity; caller owns the transaction."""
    zones, cv, ref_pace = cfg
    s.execute(delete(Lap).where(Lap.activity_id == act.id, Lap.kind == "split_km"))
    s.execute(delete(BestEffort).where(BestEffort.activity_id == act.id))
    t, d, hr, speed, alt = (ch.get(k) for k in ("time", "distance", "hr", "speed", "altitude"))
    if d:
        d = metrics.scale_distance_stream(d, act.distance_m)
    # treadmill: no real grade, distance estimated -> no GAP (§8.3)
    grades = metrics.compute_grades(d, alt) if d and alt and not act.is_indoor else None
    gap_d = metrics.compute_gap_distance(d, grades) if d and grades else None
    m = ActivityMetrics(
        activity_id=act.id,
        algo_version=metrics.ALGO_VERSION,
        computed_at=utcnow_iso(),
        zones_hash=hashlib.sha1(json.dumps(zones).encode()).hexdigest()[:12] if zones else None,
    )
    if t and d:
        splits = metrics.compute_km_splits(d, t, hr, alt)
        gaps = metrics.compute_split_gap_speeds(d, gap_d, splits) if gap_d else [None] * len(splits)
        s.add_all(
            Lap(
                activity_id=act.id,
                kind="split_km",
                idx=x.idx,
                start_offset_s=round(x.start_offset_s),
                elapsed_s=round(x.elapsed_s),
                distance_m=x.distance_m,
                avg_speed_ms=x.avg_speed_ms,
                avg_hr=x.avg_hr,
                elev_gain_m=x.elev_gain_m,
                gap_speed_ms=g,
            )
            for x, g in zip(splits, gaps, strict=True)
        )
        if not act.is_indoor:  # treadmill distance is estimated: no best efforts / EF (§8.3)
            s.add_all(
                BestEffort(
                    activity_id=act.id,
                    distance_m=b.distance_m,
                    elapsed_s=round(b.elapsed_s),
                    start_offset_s=round(b.start_offset_s),
                    algo_version=metrics.ALGO_VERSION,
                )
                for b in metrics.compute_best_efforts(d, t)
            )
    if hr and t and zones:
        m.time_in_zones_s = metrics.compute_time_in_zones(hr, t, zones)
    if not act.is_indoor and act.distance_m and act.moving_s:
        m.efficiency_factor = metrics.compute_efficiency_factor(
            act.distance_m / act.moving_s, act.avg_hr
        )
    if speed and t:
        m.is_steady, m.pace_cv = metrics.compute_steady_run(
            speed, t, bool(act.is_indoor), act.workout_type, cv
        )
        m.gps_suspect = metrics.detect_gps_suspect(speed, t)
    if t and d and hr and m.is_steady:
        m.decoupling_pct = metrics.compute_decoupling(t, gap_d or d, hr)
    if t and d and hr and not act.is_indoor:
        m.hr_at_ref_pace = metrics.compute_hr_at_pace(t, d, hr, grades, ref_pace)
    if d and gap_d and act.distance_m and act.moving_s and d[-1] > d[0]:
        m.gap_speed_ms = act.distance_m / act.moving_s * (gap_d[-1] - gap_d[0]) / (d[-1] - d[0])
    m.ef_adjusted = adjusted_ef(act, m)
    s.merge(m)


def _dedup(s: Session, start_iso: str, elapsed_s: int, dist_m: float | None, source: str) -> Any:
    start = datetime.fromisoformat(start_iso)
    lo, hi = (
        _now_iso(start + timedelta(seconds=x)) for x in (-MAX_START_DELTA_S, MAX_START_DELTA_S)
    )
    acts = s.scalars(select(Activity).where(Activity.start_time_utc.between(lo, hi))).all()
    cands = [
        ActivitySummary(
            a.id,
            datetime.fromisoformat(a.start_time_utc),
            a.elapsed_s or 0,
            a.distance_m,
            frozenset(
                s.scalars(select(SourceRecord.source).where(SourceRecord.activity_id == a.id))
            ),
        )
        for a in acts
    ]
    return find_duplicate_candidate(start, elapsed_s, dist_m, cands, source)


def _merge_missing(
    s: Session, act_id: int | None, rec: SourceRecord, draft: Any, stream: StreamDraft | None
) -> None:
    """Fill channels the host stream lacks (cadence, hr) from an attached record; nothing else."""
    host = s.get(Activity, act_id) if act_id else None
    if host is None or stream is None:
        return
    st = s.get(Stream, host.stream_source_id) if host.stream_source_id else None
    if st is None:  # host has no stream at all: adopt this one
        host.stream_source_id = rec.id
        s.flush()
        s.add(
            Stream(
                source_record_id=rec.id,
                activity_id=host.id,
                n_points=stream.n_points,
                channels=",".join(stream.channels),
                data=encode_stream(stream.channels),
                codec_version=CODEC_VERSION,
            )
        )
        ch = stream.channels
    else:
        ch = decode_stream(st.data)
        shift = (
            datetime.fromisoformat(draft.start_time_utc)
            - datetime.fromisoformat(host.start_time_utc)
        ).total_seconds()
        src_t = [x + shift for x in stream.channels["time"]]
        added = False
        for name in ("cadence", "hr"):
            if name in ch or name not in stream.channels or "time" not in ch:
                continue
            src = stream.channels[name]
            out = []
            for t in ch["time"]:
                i = bisect_right(src_t, t) - 1
                out.append(src[i] if 0 <= i and t <= src_t[-1] + 2 else None)
            ch[name], added = out, True
        if not added:
            return
        st.data, st.channels = encode_stream(ch), ",".join(ch)
    host.has_cadence = host.has_cadence or "cadence" in ch
    host.has_hr = host.has_hr or "hr" in ch
    if host.avg_cadence_spm is None:
        host.avg_cadence_spm = draft.avg_cadence_spm
    _compute_metrics(s, host, ch, _cfg(s))


class Runner:
    def __init__(
        self,
        engine: Engine,
        queue: JobQueue,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.engine, self.queue, self.now = engine, queue, now

    # --- dispatch ------------------------------------------------------
    def execute(self, job: Job) -> None:
        """Run one claimed job and record its outcome. Never raises."""
        handlers = {
            "recompute": self.run_recompute_metrics,
            "ai_sweep": self.run_ai_sweep,
            "intervals_sync": self.run_intervals_sync,
            "weather": self.run_weather,
        }
        try:
            self.queue.finish_job(job.id, "done", handlers[job.kind](job))
        except Exception as e:  # noqa: BLE001
            if _transient(e) and job.attempts <= len(BACKOFF_S):
                delay = timedelta(seconds=BACKOFF_S[job.attempts - 1])
                self.queue.requeue(job.id, self.now() + delay)
            else:
                self.queue.finish_job(job.id, "failed", f"{type(e).__name__}: {e}"[:500])

    # --- handlers: each returns an error note if some activities failed ---
    def run_intervals_sync(self, job: Job) -> str | None:
        from app.ingest import intervals  # files -> runner import cycle

        err = intervals.sync(self.engine, lambda d, t: self.queue.progress(job.id, d, t))
        queue_auto_analysis(self.engine)
        with Session(self.engine) as s:
            if (w := s.get(Setting, "weather_enabled")) and w.value:
                self.queue.enqueue("weather", {})
        return err

    def run_weather(self, job: Job) -> str | None:
        from app.ingest import weather  # weather -> runner (adjusted_ef) import cycle

        return weather.fill_missing(self.engine, lambda d, t: self.queue.progress(job.id, d, t))

    def run_ai_sweep(self, job: Job) -> str | None:
        done = threading.Event()  # a model call can outlast STALE_AFTER: keep the heartbeat going
        threading.Thread(target=self._beat, args=(job.id, done), daemon=True).start()
        try:
            return run_sweep(self.engine)
        finally:
            done.set()

    def _beat(self, job_id: int, done: threading.Event) -> None:
        while not done.wait(HEARTBEAT_INTERVAL_S):
            self.queue.heartbeat(job_id)

    def run_recompute_metrics(self, job: Job) -> str | None:
        with Session(self.engine) as s:
            ids = list(s.scalars(select(Activity.id).order_by(Activity.id)))
            cfg = _cfg(s)
        failed = 0
        self.queue.progress(job.id, 0, len(ids))
        for i, aid in enumerate(ids, 1):
            try:
                with Session(self.engine) as s, s.begin():
                    act = s.get(Activity, aid)
                    assert act is not None
                    sid = act.stream_source_id or act.primary_source_id
                    st = s.get(Stream, sid) if sid else None
                    _compute_metrics(s, act, decode_stream(st.data) if st else {}, cfg)
            except Exception:  # noqa: BLE001
                failed += 1
            self.queue.progress(job.id, i, len(ids))
        return f"{failed} activities failed" if failed else None

    # --- storing (one transaction per activity) -------------------------
    @staticmethod
    def _save_activity(
        s: Session,
        rec: SourceRecord,
        draft: Any,
        laps: list[LapDraft],
        stream: StreamDraft | None,
    ) -> None:
        act = s.get(Activity, rec.activity_id) if rec.activity_id else None
        if act is not None and act.primary_source_id != rec.id:
            return  # attached record: the primary source owns the summary
        if act is None:
            action, target = _dedup(
                s, draft.start_time_utc, draft.elapsed_s or 0, draft.distance_m, rec.source
            )
            if action == DedupAction.ATTACH_TO_EXISTING:
                rec.activity_id, rec.status = target, "duplicate"
                _merge_missing(s, target, rec, draft, stream)
                return
            act = Activity(primary_source_id=rec.id)
            if action == DedupAction.FLAG_DUPLICATE:
                act.duplicate_of_id, act.excluded_from_stats = target, True
            for f in ACTIVITY_FIELDS:
                setattr(act, f, getattr(draft, f))
            s.add(act)
        else:
            for f in ACTIVITY_FIELDS:
                if f != "workout_type":  # local after first import (PLAN §7)
                    setattr(act, f, getattr(draft, f))
        act.stream_source_id = rec.id if stream else None
        s.flush()
        rec.activity_id, rec.status = act.id, "mapped"
        s.execute(delete(Lap).where(Lap.activity_id == act.id, Lap.kind == "device_lap"))
        s.execute(delete(Stream).where(Stream.source_record_id == rec.id))
        s.add_all(Lap(activity_id=act.id, **asdict(lp)) for lp in laps)
        if stream:
            s.add(
                Stream(
                    source_record_id=rec.id,
                    activity_id=act.id,
                    n_points=stream.n_points,
                    channels=",".join(stream.channels),
                    data=encode_stream(stream.channels),
                    codec_version=CODEC_VERSION,
                )
            )
        _compute_metrics(s, act, stream.channels if stream else {}, _cfg(s))
