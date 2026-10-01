"""Uploaded files -> canonical activities (PLAN M7-01): FIT/GPX/TCX (+ .gz), Strava bulk-export
zip, Health Auto Export JSON/zip. Parsed synchronously in the API request."""

import csv
import gzip
import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime
from functools import partial
from itertools import pairwise
from pathlib import PurePosixPath
from statistics import fmean, median
from typing import Any
from zoneinfo import ZoneInfo

import fitdecode  # type: ignore[import-untyped]
from defusedxml import ElementTree  # type: ignore[import-untyped]
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.domain.models import utcnow_iso
from app.ingest.hae import TZ, _gain, _haversine, import_workouts
from app.ingest.strava_mapper import CADENCE_FACTOR, ActivityDraft, StreamDraft
from app.worker.runner import Runner, _record

MAX_FILE = 50 * 1024 * 1024  # decompressed size of a single activity file (anti zip-bomb)
FORMATS = {".fit": "file_fit", ".gpx": "file_gpx", ".tcx": "file_tcx"}
SEMI = 180 / 2**31  # FIT semicircles -> degrees
XML_KEYS = {  # GPX / TCX local tag names -> point key
    "time": "t", "Time": "t",
    "lat": "lat", "LatitudeDegrees": "lat",
    "lon": "lng", "LongitudeDegrees": "lng",
    "ele": "alt", "AltitudeMeters": "alt",
    "hr": "hr", "Value": "hr",
    "cad": "cad", "RunCadence": "cad",
    "DistanceMeters": "dist",
    "Speed": "speed",
    "power": "power", "Watts": "power",
}  # fmt: skip
RUN_WORDS = ("run", "corsa", "9")  # GPX <type>: "running", localized, or Strava's old numeric 9


class Rejected(ValueError):
    pass


class _FitProc(fitdecode.DefaultDataProcessor):  # type: ignore[misc]
    def on_process_type(self, reader: Any, field_data: Any) -> None:
        if not isinstance(field_data.value, tuple):  # fitdecode crashes on (date)time arrays
            super().on_process_type(reader, field_data)


def _ln(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _ungz(name: str, data: bytes) -> tuple[str, bytes]:
    if not name.lower().endswith(".gz"):
        return name, data
    out = gzip.GzipFile(fileobj=io.BytesIO(data)).read(MAX_FILE + 1)
    if len(out) > MAX_FILE:
        raise Rejected(f"{name}: decompressed file too large")
    return name[:-3], out


# --- parsers: -> (points, meta). Point keys: t lat lng alt hr cad dist speed power -----------
def _parse_fit(data: bytes) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if data[8:12] != b".FIT":
        raise Rejected("not a FIT file")
    pts: list[dict[str, Any]] = []
    meta: dict[str, Any] = {}
    with fitdecode.FitReader(io.BytesIO(data), processor=_FitProc()) as fr:
        for f in fr:
            if not isinstance(f, fitdecode.FitDataMessage):
                continue
            g = partial(_fv, f)
            if f.name == "record" and g("timestamp"):
                lat, lng = g("position_lat"), g("position_long")
                cad = g("cadence")
                pts.append(
                    {
                        "t": g("timestamp"),
                        "lat": lat * SEMI if lat is not None else None,
                        "lng": lng * SEMI if lng is not None else None,
                        "alt": g("enhanced_altitude") or g("altitude"),
                        "hr": g("heart_rate"),
                        "cad": (cad + (g("fractional_cadence") or 0)) * CADENCE_FACTOR
                        if cad is not None
                        else None,
                        "dist": g("distance"),
                        "speed": g("enhanced_speed") or g("speed"),
                        "power": g("power"),
                    }
                )
            elif f.name == "session":
                meta |= {
                    "sport": str(g("sport") or ""),
                    "indoor": str(g("sub_sport") or "") in ("treadmill", "indoor_running"),
                    "distance_m": g("total_distance"),
                    "elapsed_s": g("total_elapsed_time"),
                    "moving_s": g("total_timer_time"),
                    "calories_kcal": g("total_calories"),
                    "elev_gain_m": g("total_ascent"),
                }
    return pts, meta


def _parse_xml(data: bytes, point_tag: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        root = ElementTree.fromstring(data)  # defusedxml: rejects entities / DTDs
    except Exception as e:
        raise Rejected(f"invalid XML: {type(e).__name__}") from e
    pts = []
    for el in root.iter():
        if _ln(el.tag) == point_tag:
            raw = {_ln(c.tag): c.text.strip() for c in el.iter() if c.text and c.text.strip()}
            raw |= el.attrib
            p: dict[str, Any] = {XML_KEYS[k]: v for k, v in raw.items() if k in XML_KEYS}
            if "t" not in p:
                continue
            p["t"] = datetime.fromisoformat(p["t"])
            for k in p.keys() - {"t"}:
                p[k] = float(p[k])
            if "cad" in p:
                p["cad"] *= CADENCE_FACTOR
            pts.append(p)
    act = root.find(".//{*}Activity")  # TCX
    typ, name = root.find(".//{*}trk/{*}type"), root.find(".//{*}trk/{*}name")  # GPX
    meta = {
        "sport": act.get("Sport") if act is not None else typ.text if typ is not None else None,
        "name": name.text.strip() if name is not None and name.text else None,
    }
    return pts, meta


def _fv(f: Any, k: str) -> Any:
    v = f.get_value(k, fallback=None)
    return None if isinstance(v, tuple) else v  # malformed array fields (seen in Strava FITs)


def _col(pts: list[dict[str, Any]], k: str) -> list[Any]:
    return [p.get(k) for p in pts]


def _hold(v: list[Any], t: list[float], gap: float = 15) -> list[Any]:
    """Sensors report sparsely (e.g. HR every ~5 s): carry the last value forward up to `gap` s."""
    out, last, lt = [], None, 0.0
    for x, ti in zip(v, t, strict=True):
        if x is not None:
            last, lt = x, ti
        out.append(x if x is not None else last if last is not None and ti - lt <= gap else None)
    return out


def _despike(v: list[float | None], w: int = 2) -> list[float | None]:
    """Rolling median (window 2w+1): drops isolated 0/2x speed glitches, keeps real changes."""
    return [
        median(win) if (win := [x for x in v[max(0, i - w) : i + w + 1] if x is not None]) else None
        for i in range(len(v))
    ]


def _avg(v: list[Any]) -> float | None:
    v = [x for x in v if x]
    return round(fmean(v), 1) if v else None


def build_draft(
    ext_id: str, pts: list[dict[str, Any]], meta: dict[str, Any]
) -> tuple[ActivityDraft, StreamDraft | None]:
    pts = sorted(pts, key=lambda p: p["t"])
    if not pts:
        raise Rejected("no track points")
    start = pts[0]["t"]
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    start_iso = start.astimezone(UTC).isoformat(timespec="seconds")
    sport = str(meta.get("sport") or "running").lower()
    if not any(w in sport for w in RUN_WORDS):
        return ActivityDraft("skipped", ext_id, source_start_time_utc=start_iso), None

    col = partial(_col, pts)
    t = [(p["t"].replace(tzinfo=p["t"].tzinfo or UTC) - start).total_seconds() for p in pts]
    has_gps = any(x is not None for x in col("lat"))
    d = col("dist")
    if any(x is None for x in d):
        d = [0.0]
        for a, b in pairwise(pts):
            ok = None not in (a.get("lat"), b.get("lat"))
            d.append(d[-1] + (_haversine((a["lat"], a["lng"]), (b["lat"], b["lng"])) if ok else 0))
    speed = col("speed")
    if any(x is None for x in speed):
        speed = [0.0] + [
            (d1 - d0) / (t1 - t0) if t1 > t0 else 0.0 for (d0, d1), (t0, t1) in
            zip(pairwise(d), pairwise(t), strict=True)
        ]  # fmt: skip
    speed = _despike(speed)
    # ponytail: moving = gaps <= 30 s at >= 0.5 m/s; FIT timer time overrides it when present
    moving = sum(
        t1 - t0
        for (t0, t1), v in zip(pairwise(t), speed[1:], strict=True)
        if t1 - t0 <= 30 and (v or 0) >= 0.5
    )
    ch: dict[str, list[Any]] = {"time": t, "distance": d, "speed": speed}
    for k, name in (("lat", "lat"), ("lng", "lng"), ("alt", "altitude"), ("hr", "hr"),
                    ("cad", "cadence"), ("power", "power")):  # fmt: skip
        if any(x is not None for x in col(k)):
            ch[name] = _hold(col(k), t) if k in ("hr", "cad", "power") else col(k)
    alt = [x for x in col("alt") if x is not None]
    hr = [x for x in col("hr") if x]
    m = {k: v for k, v in meta.items() if v is not None}
    elapsed = round(m.get("elapsed_s", t[-1]))
    avg_cad = _avg(col("cad"))
    indoor = bool(m.get("indoor")) or not has_gps
    local_date = start.astimezone(ZoneInfo(TZ)).date().isoformat()
    draft = ActivityDraft(
        status="mapped",
        external_id=ext_id,
        source_start_time_utc=start_iso,
        sport_type="treadmill" if indoor else "run",
        name=m.get("name") or f"Run {local_date}",  # FIT carries no title
        start_time_utc=start_iso,
        timezone=TZ,
        local_date=local_date,
        elapsed_s=elapsed,
        moving_s=round(m.get("moving_s", moving if moving > 0 else elapsed)),
        distance_m=m.get("distance_m", d[-1]),
        elev_gain_m=m.get("elev_gain_m", _gain(alt) if alt else None),
        avg_hr=_avg(hr),
        max_hr=float(max(hr)) if hr else None,
        avg_cadence_spm=avg_cad,
        avg_power_w=_avg(col("power")),
        calories_kcal=m.get("calories_kcal"),
        has_gps=has_gps,
        has_hr=bool(hr),
        has_cadence=avg_cad is not None,
        is_indoor=indoor,
        workout_type="easy",
    )
    return draft, StreamDraft(ch)


def store_file(engine: Engine, name: str, data: bytes, meta: dict[str, Any] | None = None) -> str:
    """One activity file (FIT/GPX/TCX, optionally .gz). Idempotent on content sha256."""
    name, data = _ungz(name, data)
    ext = PurePosixPath(name.lower()).suffix
    if ext not in FORMATS:
        raise Rejected(f"{name}: unsupported format")
    pts, m = (
        _parse_fit(data)
        if ext == ".fit"
        else _parse_xml(data, "trkpt" if ext == ".gpx" else "Trackpoint")
    )
    m |= {k: v for k, v in (meta or {}).items() if v}
    sha = hashlib.sha256(data).hexdigest()
    draft, stream = build_draft(sha, pts, m)
    with Session(engine) as s, s.begin():
        rec = _record(s, sha, FORMATS[ext])
        rec.raw_detail = {
            "filename": PurePosixPath(name).name,
            "sha256": sha,
            "sport": m.get("sport"),
        }
        rec.source_start_time_utc, rec.mapper_version = draft.source_start_time_utc, 1
        rec.fetched_at, rec.error = utcnow_iso(), None
        if draft.status == "skipped":
            rec.status = "skipped"
        else:
            Runner._save_activity(s, rec, draft, [], stream)
        return rec.status


def _strava_zip(engine: Engine, z: zipfile.ZipFile) -> dict[str, Any]:
    """Strava bulk export: activities.csv + activities/<id>.(fit|gpx|tcx)[.gz]."""
    meta: dict[str, dict[str, Any]] = {}
    if "activities.csv" in z.namelist():
        rows = csv.DictReader(io.StringIO(z.read("activities.csv").decode("utf-8-sig")))
        for r in rows:
            if r.get("Filename"):
                meta[r["Filename"]] = {
                    "name": r.get("Activity Name"),
                    "sport": r.get("Activity Type"),
                }
    out: dict[str, Any] = {"mapped": 0, "skipped": 0, "duplicate": 0, "failed": []}
    for info in z.infolist():
        n = info.filename
        if not n.startswith("activities/") or info.is_dir():
            continue
        try:
            if info.file_size > MAX_FILE:
                raise Rejected("file too large")
            st = store_file(engine, n, z.read(info), meta.get(n))
        except Exception as e:  # noqa: BLE001
            out["failed"].append({"id": n, "error": f"{type(e).__name__}: {e}"[:300]})
        else:
            out[st] = out.get(st, 0) + 1
    return out


def import_upload(engine: Engine, name: str, data: bytes) -> dict[str, Any]:
    """Dispatch one uploaded file by type. Returns {mapped, skipped, duplicate, failed[]}."""
    low = name.lower()
    if low.endswith(".zip"):
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as e:
            raise Rejected("invalid zip") from e
        names = z.namelist()
        if "activities.csv" in names or any(n.startswith("activities/") for n in names):
            return _strava_zip(engine, z)
        js = next((i for i in z.infolist() if i.filename.lower().endswith(".json")), None)
        if js is None:
            raise Rejected("zip contains neither a Strava export nor a Health Auto Export JSON")
        if js.file_size > 5 * MAX_FILE:
            raise Rejected("JSON too large")
        data, low = z.read(js), ".json"
    if low.endswith(".json"):
        try:
            workouts = json.loads(data)["data"]["workouts"]
        except (ValueError, KeyError, TypeError) as e:
            raise Rejected("expected Health Auto Export JSON: data.workouts[]") from e
        return import_workouts(engine, workouts)
    st = store_file(engine, name, data)
    return {"mapped": 0, "skipped": 0, "duplicate": 0, "failed": []} | {st: 1}
