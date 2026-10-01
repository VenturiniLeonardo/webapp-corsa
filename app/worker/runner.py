"""Job handlers + scheduler (PLAN §8.1, §8.5, §9.3). One DB transaction per activity."""

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
from app.api.strava_auth import PROVIDER
from app.core.config import get_settings
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
    ProviderAccount,
    Setting,
    SourceRecord,
    Stream,
    utcnow_iso,
)
from app.domain.stream_codec import decode_stream, encode_stream
from app.ingest.strava_client import RateLimitWaitException, StravaClient
from app.ingest.strava_mapper import RUN_SPORTS, LapDraft, StreamDraft, map_strava_activity
from app.metrics import engine as metrics
from app.worker.queue import HEARTBEAT_INTERVAL_S, JobQueue

PAGE = 200
RECENT_WINDOW = timedelta(days=30)
CURSOR_OVERLAP_S = 3600
BACKOFF_S = (10, 60, 300)
CODEC_VERSION = 1
STREAM_KEYS = [
    "time",
    "distance",
    "latlng",
    "altitude",
    "velocity_smooth",
    "heartrate",
    "cadence",
    "watts",
    "moving",
]
# Summary fields whose change triggers a refetch (PLAN §9.3); kudos etc. change constantly.
CHANGE_KEYS = ("name", "distance", "sport_type", "moving_time", "elapsed_time", "workout_type")
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
SCHEDULE = {"strava_sync": timedelta(minutes=30), "strava_reconcile": timedelta(days=7)}

Cfg = tuple[list[float] | None, float]


def _now_iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def _is_run(summary: dict[str, Any]) -> bool:
    return (summary.get("sport_type") or summary.get("type")) in RUN_SPORTS


def _changed(old: dict[str, Any] | None, new: dict[str, Any]) -> bool:
    return old is None or any(old.get(k) != new.get(k) for k in CHANGE_KEYS)


def _transient(e: Exception) -> bool:
    if isinstance(e, httpx.HTTPStatusError):
        return e.response.status_code >= 500
    return isinstance(e, httpx.TransportError)


def _cfg(s: Session) -> Cfg:
    v: dict[str, Any] = dict(
        s.execute(
            select(Setting.key, Setting.value).where(
                Setting.key.in_(("hr_zones", "steady_cv_threshold"))
            )
        ).all()
    )
    return v.get("hr_zones") or None, float(v.get("steady_cv_threshold") or 0.08)


def _record(s: Session, ext_id: str, source: str = PROVIDER) -> SourceRecord:
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


def _compute_metrics(s: Session, act: Activity, ch: dict[str, list[Any]], cfg: Cfg) -> None:
    """Derived rows for one activity; caller owns the transaction."""
    zones, cv = cfg
    s.execute(delete(Lap).where(Lap.activity_id == act.id, Lap.kind == "split_km"))
    s.execute(delete(BestEffort).where(BestEffort.activity_id == act.id))
    t, d, hr, speed = (ch.get(k) for k in ("time", "distance", "hr", "speed"))
    m = ActivityMetrics(
        activity_id=act.id,
        algo_version=metrics.ALGO_VERSION,
        computed_at=utcnow_iso(),
        zones_hash=hashlib.sha1(json.dumps(zones).encode()).hexdigest()[:12] if zones else None,
    )
    if t and d:
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
            )
            for x in metrics.compute_km_splits(d, t, hr, ch.get("altitude"))
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
    s.merge(m)


def _dedup(
    s: Session, start_iso: str, elapsed_s: int, dist_m: float | None, source: str = PROVIDER
) -> Any:
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
        client: StravaClient,
        queue: JobQueue,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.engine, self.client, self.queue, self.now = engine, client, queue, now

    # --- dispatch ------------------------------------------------------
    def execute(self, job: Job) -> None:
        """Run one claimed job and record its outcome. Never raises."""
        handlers = {
            "strava_backfill": self.run_strava_backfill,
            "strava_sync": self.run_strava_sync,
            "strava_reconcile": self.run_strava_reconcile,
            "recompute": self.run_recompute_metrics,
            "ai_sweep": self.run_ai_sweep,
        }
        try:
            self.queue.finish_job(job.id, "done", handlers[job.kind](job))
        except RateLimitWaitException as e:
            self.queue.requeue(job.id, e.resume_at, refund_attempt=True)
        except Exception as e:  # noqa: BLE001
            if _transient(e) and job.attempts <= len(BACKOFF_S):
                delay = timedelta(seconds=BACKOFF_S[job.attempts - 1])
                self.queue.requeue(job.id, self.now() + delay)
            else:
                self.queue.finish_job(job.id, "failed", f"{type(e).__name__}: {e}"[:500])

    # --- handlers: each returns an error note if some activities failed ---
    def run_strava_backfill(self, job: Job) -> str | None:
        # Strava lists newest first, i.e. we walk backwards. Re-runs skip what is already imported.
        return self._ingest_all(job, self._list(), retry_errors=True)

    def run_strava_sync(self, job: Job) -> str | None:
        with Session(self.engine) as s:
            acc = s.get(ProviderAccount, PROVIDER)
            cursor = acc.sync_cursor if acc else None
        # One list covers both "new since cursor-1h" and "last 30 days" (whichever reaches further).
        window = int((self.now() - RECENT_WINDOW).timestamp())
        after = min(cursor - CURSOR_OVERLAP_S, window) if cursor else None
        err = self._ingest_all(job, self._list(after), retry_errors=False)
        queue_auto_analysis(self.engine)
        self._touch("last_sync_at")
        self._ping()
        return err

    def run_strava_reconcile(self, job: Job) -> None:
        ids = {str(x["id"]) for x in self._list()}
        with Session(self.engine) as s, s.begin():
            rows = s.execute(
                select(Activity, SourceRecord.external_id)
                .join(SourceRecord, Activity.primary_source_id == SourceRecord.id)
                .where(SourceRecord.source == PROVIDER, Activity.upstream_deleted_at.is_(None))
            ).all()
            gone = [a for a, ext in rows if ext not in ids]
            if rows and not ids:  # empty listing = API/scope glitch, not "everything deleted"
                raise RuntimeError("empty upstream listing; refusing to mark deletions")
            for a in gone:  # never hard delete (§9.3)
                a.upstream_deleted_at = _now_iso(self.now())
                a.excluded_from_stats = True
        self._touch("last_reconcile_at")

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

    # --- fetching ------------------------------------------------------
    def _list(self, after: int | None = None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        page = 1
        while True:
            batch = self.client.get_athlete_activities(after=after, page=page, per_page=PAGE)
            out += batch
            if len(batch) < PAGE:
                return out
            page += 1

    def _ingest_all(
        self, job: Job, summaries: list[dict[str, Any]], retry_errors: bool
    ) -> str | None:
        with Session(self.engine) as s:
            known = {
                ext: (st, raw)
                for ext, st, raw in s.execute(
                    select(
                        SourceRecord.external_id, SourceRecord.status, SourceRecord.raw_summary
                    ).where(SourceRecord.source == PROVIDER)
                )
            }
        failed = 0
        self.queue.progress(job.id, 0, len(summaries))
        for i, x in enumerate(summaries, 1):
            st, old = known.get(str(x["id"]), (None, None))
            todo = st is None or st == "pending" or (st == "error" and retry_errors)
            if (todo or _changed(old, x)) and not self._ingest(x, job.id):
                failed += 1
            self.queue.progress(job.id, i, len(summaries))
        return f"{failed} activities failed" if failed else None

    def _ingest(self, summary: dict[str, Any], job_id: int) -> bool:
        """Fetch (may raise rate-limit/HTTP errors: nothing is open yet), then store atomically."""
        detail = streams = None
        if _is_run(summary):
            detail = self.client.get_activity_detail(summary["id"])
            try:
                streams = self.client.get_activity_streams(summary["id"], STREAM_KEYS)
            except httpx.HTTPStatusError as e:
                if e.response.status_code != 404:  # 404 = activity without streams
                    raise
        try:
            self._store(summary, detail, streams, job_id)
            return True
        except Exception as e:  # noqa: BLE001
            self._fail(summary, e, job_id)
            return False

    # --- storing (one transaction per activity) -------------------------
    def _store(
        self,
        summary: dict[str, Any],
        detail: dict[str, Any] | None,
        streams: dict[str, Any] | None,
        job_id: int | None,
    ) -> None:
        draft, laps, stream = map_strava_activity(detail or summary, streams)
        with Session(self.engine) as s, s.begin():
            rec = _record(s, draft.external_id)
            rec.raw_summary, rec.raw_detail = summary, detail
            rec.source_start_time_utc, rec.mapper_version = (
                draft.source_start_time_utc,
                draft.mapper_version,
            )
            rec.fetched_at, rec.job_id, rec.error = utcnow_iso(), job_id, None
            if draft.status == "skipped":
                rec.status = "skipped"
            else:
                self._save_activity(s, rec, draft, laps, stream)
            acc = s.get(ProviderAccount, PROVIDER)
            epoch = int(datetime.fromisoformat(str(draft.source_start_time_utc)).timestamp())
            if acc and (acc.sync_cursor or 0) < epoch:
                acc.sync_cursor = epoch

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

    def _fail(self, summary: dict[str, Any], err: Exception, job_id: int) -> None:
        with Session(self.engine) as s, s.begin():
            rec = _record(s, str(summary["id"]))
            rec.status, rec.error = "error", f"{type(err).__name__}: {err}"[:500]
            rec.raw_summary, rec.job_id = summary, job_id

    # --- account bookkeeping -------------------------------------------
    def _touch(self, column: str) -> None:
        with Session(self.engine) as s, s.begin():
            if acc := s.get(ProviderAccount, PROVIDER):
                setattr(acc, column, _now_iso(self.now()))

    def _ping(self) -> None:
        if url := get_settings().HEALTHCHECK_URL_SYNC:
            try:
                httpx.get(url, timeout=10)
            except httpx.HTTPError:
                pass  # a monitoring hiccup must not fail the sync


class Scheduler:
    """In-process periodic enqueue; `enqueue` already dedups against queued/running jobs."""

    def __init__(
        self,
        engine: Engine,
        queue: JobQueue,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.engine, self.queue, self.now = engine, queue, now
        t = now()
        acc = self._account()
        last = acc.last_reconcile_at if acc else None
        self.next = {
            "strava_sync": t,
            "strava_reconcile": datetime.fromisoformat(last) + SCHEDULE["strava_reconcile"]
            if last
            else t,
        }

    def _account(self) -> ProviderAccount | None:
        with Session(self.engine) as s:
            return s.get(ProviderAccount, PROVIDER)

    def tick(self) -> None:
        t = self.now()
        for kind, every in SCHEDULE.items():
            if t < self.next[kind]:
                continue
            self.next[kind] = t + every
            acc = self._account()
            if acc and acc.refresh_token and acc.status in (None, "active"):
                self.queue.enqueue(kind, {})
