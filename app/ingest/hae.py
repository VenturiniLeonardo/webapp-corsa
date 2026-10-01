"""Health Auto Export workout JSON -> canonical activity (PLAN §13 option D). Mapper + store."""

import json
import sys
import zipfile
from datetime import UTC, datetime
from itertools import pairwise
from math import asin, cos, radians, sin, sqrt
from pathlib import Path
from typing import Any

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.domain.models import utcnow_iso
from app.ingest.strava_mapper import ActivityDraft, StreamDraft
from app.worker.runner import Runner, _record

SOURCE = "apple_health"
MAPPER_VERSION = 1
TZ = "Europe/Rome"  # ponytail: export carries only a UTC offset; single-user, fixed home zone
KJ_PER_KCAL = 4.184


def _t(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S %z")


def _q(w: dict[str, Any], key: str) -> float | None:
    v = w.get(key)
    return float(v["qty"]) if isinstance(v, dict) and v.get("qty") is not None else None


def _haversine(a: tuple[float, float], b: tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(radians, (*a, *b))
    h = sin((la2 - la1) / 2) ** 2 + cos(la1) * cos(la2) * sin((lo2 - lo1) / 2) ** 2
    return 12742000 * asin(sqrt(h))


def _gain(alt: list[float], hyst: float = 1.0) -> float:
    """Elevation gain ignoring wiggles below `hyst` metres."""
    gain, ref = 0.0, alt[0]
    for a in alt[1:]:
        if a - ref >= hyst:
            gain, ref = gain + a - ref, a
        elif ref - a >= hyst:
            ref = a
    return gain


def _bucketed(
    items: list[dict[str, Any]], key: str, start: datetime, end: datetime, per_min: bool
) -> list[tuple[float, float]]:
    """(offset s, value) from minute buckets; step counts are scaled to steps/min."""
    out = []
    for i in items:
        b = _t(i["date"])
        v = float(i[key])
        if per_min:  # first/last bucket only partly overlap the workout
            span = min(end.timestamp(), b.timestamp() + 60) - max(start.timestamp(), b.timestamp())
            if span < 5:
                continue
            v = v * 60 / span
        out.append(((b - start).total_seconds(), v))
    return sorted(out)


def _step(samples: list[tuple[float, float]], t: list[float]) -> list[float | None]:
    """Value of the last sample at or before each t (t ascending)."""
    out: list[float | None] = []
    j, cur = 0, None
    for x in t:
        while j < len(samples) and samples[j][0] <= x:
            cur = samples[j][1]
            j += 1
        out.append(cur)
    return out


def map_hae_workout(w: dict[str, Any]) -> tuple[ActivityDraft, StreamDraft | None]:
    ext_id = str(w["id"])
    start, end = _t(w["start"]), _t(w["end"])
    start_iso = start.astimezone(UTC).isoformat(timespec="seconds")
    if "Run" not in w.get("name", ""):
        return ActivityDraft("skipped", ext_id, source_start_time_utc=start_iso), None

    dist = w.get("distance") or {}
    dist_m = float(dist["qty"]) * (1000 if dist.get("units") == "km" else 1) if dist else None
    kj = _q(w, "activeEnergyBurned")
    elapsed = round(float(w["duration"]))

    route = sorted(w.get("route") or [], key=lambda p: p["timestamp"])
    stream = None
    gain = _q(w, "elevationUp")
    if route:
        t = [(_t(p["timestamp"]) - start).total_seconds() for p in route]
        pts = [(p["latitude"], p["longitude"]) for p in route]
        d = [0.0]
        for a, b in pairwise(pts):
            d.append(d[-1] + _haversine(a, b))
        alt = [p["altitude"] for p in route]
        ch: dict[str, list[Any]] = {
            "time": t,
            "distance": d,
            "lat": [p[0] for p in pts],
            "lng": [p[1] for p in pts],
            "altitude": alt,
            "speed": [p.get("speed") for p in route],
        }
        if w.get("heartRateData"):
            ch["hr"] = _step(_bucketed(w["heartRateData"], "Avg", start, end, False), t)
        if w.get("stepCount"):
            ch["cadence"] = _step(_bucketed(w["stepCount"], "qty", start, end, True), t)
        stream = StreamDraft(ch)
        if gain is None:
            gain = _gain(alt)

    avg_cad = _q(w, "stepCadence")
    draft = ActivityDraft(
        status="mapped",
        external_id=ext_id,
        source_start_time_utc=start_iso,
        mapper_version=MAPPER_VERSION,
        sport_type="treadmill" if w.get("isIndoor") else "run",
        name=w.get("name"),
        start_time_utc=start_iso,
        timezone=TZ,
        local_date=w["start"][:10],
        elapsed_s=elapsed,
        moving_s=elapsed,  # ponytail: pauses ignored; derive from segments if auto-pause matters
        distance_m=dist_m,
        elev_gain_m=gain,
        avg_hr=_q(w, "avgHeartRate"),
        max_hr=_q(w, "maxHeartRate"),
        avg_cadence_spm=avg_cad,
        calories_kcal=round(kj / KJ_PER_KCAL) if kj is not None else None,
        has_gps=bool(route),
        has_hr=bool(w.get("heartRateData")) or _q(w, "avgHeartRate") is not None,
        has_cadence=avg_cad is not None,
        is_indoor=bool(w.get("isIndoor")),
        workout_type="easy",
    )
    return draft, stream


def store_workout(engine: Engine, w: dict[str, Any]) -> str:
    """One atomic transaction per workout; idempotent on the HAE workout id. Returns status."""
    draft, stream = map_hae_workout(w)
    with Session(engine) as s, s.begin():
        rec = _record(s, draft.external_id, SOURCE)
        rec.raw_detail = {k: v for k, v in w.items() if k != "route"}  # route lives in the stream
        rec.source_start_time_utc, rec.mapper_version = draft.source_start_time_utc, MAPPER_VERSION
        rec.fetched_at, rec.error = utcnow_iso(), None
        if draft.status == "skipped":
            rec.status = "skipped"
        else:
            Runner._save_activity(s, rec, draft, [], stream)
        return rec.status


def import_workouts(engine: Engine, workouts: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {"mapped": 0, "skipped": 0, "duplicate": 0, "failed": []}
    for w in workouts:
        try:
            st = store_workout(engine, w)
        except Exception as e:  # noqa: BLE001
            out["failed"].append({"id": w.get("id"), "error": f"{type(e).__name__}: {e}"[:300]})
        else:
            out[st] = out.get(st, 0) + 1
    return out


def main(path: str) -> None:
    """Bulk import: `python -m app.ingest.hae export.json|export.zip`."""
    from app.core.config import get_settings
    from app.core.db import make_engine

    p = Path(path)
    if p.suffix == ".zip":
        with zipfile.ZipFile(p) as z:
            doc = json.loads(z.read(next(n for n in z.namelist() if n.endswith(".json"))))
    else:
        doc = json.loads(p.read_text(encoding="utf-8"))
    print(import_workouts(make_engine(get_settings().DATABASE_URL), doc["data"]["workouts"]))


if __name__ == "__main__":
    main(sys.argv[1])
