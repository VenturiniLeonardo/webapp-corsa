"""One-off import of runs from a Strava account export (zip) through the normal store pipeline.

    python scripts/import_strava_export.py export_data.zip [--tz Europe/Rome] [--limit N]

Rows are converted to Strava-API-shaped dicts, so `source_records` get the real Strava activity id
and a later API sync dedups against them. Re-runnable: already mapped activities are skipped.
"""

import argparse
import csv
import gzip
import io
import math
import sys
import zipfile
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

from defusedxml import ElementTree

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

RUN_TYPES = {"Run", "TrailRun", "VirtualRun"}
CHANNELS = ("distance", "altitude", "velocity_smooth", "heartrate", "cadence", "watts")
# (record key, stream key); latlng is handled separately
KEYS = (
    ("dist", "distance"),
    ("alt", "altitude"),
    ("speed", "velocity_smooth"),
    ("hr", "heartrate"),
)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _num(v: str) -> float | None:
    return float(v) if v.strip() else None


def _haversine(a: tuple[float, float], b: tuple[float, float]) -> float:
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    h = (
        math.sin((p2 - p1) / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(math.radians(b[1] - a[1]) / 2) ** 2
    )
    return 12_742_000 * math.asin(math.sqrt(h))


def _fill(v: list[Any]) -> list[Any] | None:
    """Forward-fill gaps (leading ones from the first value); None if the channel never appears."""
    first = next((x for x in v if x is not None), None)
    if first is None:
        return None
    out, last = [], first
    for x in v:
        last = last if x is None else x
        out.append(last)
    return out


def _streams(recs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Per-sample records {t, lat, lng, dist, alt, speed, hr, cad, pwr} -> Strava key_by_type streams."""
    recs = sorted((r for r in recs if r.get("t") is not None), key=lambda r: r["t"])
    if len(recs) < 2:
        return None
    t0 = recs[0]["t"]
    out: dict[str, Any] = {"time": [round((r["t"] - t0).total_seconds()) for r in recs]}
    pts = _fill([(r["lat"], r["lng"]) if r.get("lat") is not None else None for r in recs])
    if pts:
        out["latlng"] = [list(p) for p in pts]
    for src, dst in (*KEYS, ("cad", "cadence"), ("pwr", "watts")):
        if (v := _fill([r.get(src) for r in recs])) is not None:
            out[dst] = v
    if "distance" not in out and pts:
        d = [0.0]
        for a, b in pairwise(pts):
            d.append(d[-1] + _haversine(a, b))
        out["distance"] = d
    if "velocity_smooth" not in out and "distance" in out:
        t, d = out["time"], out["distance"]
        out["velocity_smooth"] = [0.0] + [
            (d[i] - d[i - 1]) / max(t[i] - t[i - 1], 1) for i in range(1, len(t))
        ]
    return out


def parse_gpx(data: bytes) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    recs: list[dict[str, Any]] = []
    for pt in ElementTree.fromstring(data).iter():
        if pt.tag.rsplit("}", 1)[-1] != "trkpt":
            continue
        r: dict[str, Any] = {"lat": float(pt.get("lat", "")), "lng": float(pt.get("lon", ""))}
        for e in pt.iter():
            name, txt = e.tag.rsplit("}", 1)[-1], (e.text or "").strip()
            if not txt:
                continue
            if name == "ele":
                r["alt"] = float(txt)
            elif name == "time":
                r["t"] = _utc(datetime.fromisoformat(txt))
            elif name == "hr":
                r["hr"] = float(txt)
            elif name == "cad":
                r["cad"] = float(txt)
        recs.append(r)
    return _streams(recs), []  # GPX has no device laps


def parse_fit(data: bytes) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    import fitdecode  # lazy: only needed for .fit files

    recs: list[dict[str, Any]] = []
    laps: list[dict[str, Any]] = []
    with fitdecode.FitReader(io.BytesIO(data)) as fr:
        for f in fr:
            if not isinstance(f, fitdecode.FitDataMessage):
                continue
            g = lambda k, f=f: f.get_value(k, fallback=None)
            if f.name == "record":
                lat, lng = g("position_lat"), g("position_long")
                recs.append(
                    {
                        "t": _utc(g("timestamp")) if g("timestamp") else None,
                        "lat": lat * 180 / 2**31 if lat is not None else None,
                        "lng": lng * 180 / 2**31 if lng is not None else None,
                        "dist": g("distance"),
                        "alt": g("enhanced_altitude")
                        if g("enhanced_altitude") is not None
                        else g("altitude"),
                        "speed": g("enhanced_speed")
                        if g("enhanced_speed") is not None
                        else g("speed"),
                        "hr": g("heart_rate"),
                        "cad": g("cadence"),
                        "pwr": g("power"),
                    }
                )
            elif f.name == "lap" and g("start_time"):
                laps.append(
                    {
                        "start_date": _iso(_utc(g("start_time"))),
                        "elapsed_time": g("total_elapsed_time"),
                        "moving_time": g("total_timer_time"),
                        "distance": g("total_distance"),
                        "average_speed": g("enhanced_avg_speed") or g("avg_speed"),
                        "average_heartrate": g("avg_heart_rate"),
                        "max_heartrate": g("max_heart_rate"),
                        "average_cadence": g("avg_cadence"),
                        "total_elevation_gain": g("total_ascent"),
                    }
                )
    return _streams(recs), laps


def read_track(
    z: zipfile.ZipFile, filename: str
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    data = z.read(filename)
    if filename.endswith(".gz"):
        data, filename = gzip.decompress(data), filename[:-3]
    return (parse_gpx if filename.endswith(".gpx") else parse_fit)(data)


def summary_from_row(row: dict[str, str], tz: str, trainer: bool) -> dict[str, Any]:
    # csv.DictReader keeps the LAST of the duplicated headers: the precise (meters/seconds) columns.
    start = datetime.strptime(row["Activity Date"], "%b %d, %Y, %I:%M:%S %p").replace(tzinfo=UTC)
    return {
        "id": int(row["Activity ID"]),
        "name": row["Activity Name"],
        "sport_type": row["Activity Type"],
        "start_date": _iso(start),
        "timezone": tz,
        "trainer": trainer,
        "distance": _num(row["Distance"]),
        "elapsed_time": _num(row["Elapsed Time"]),
        "moving_time": _num(row["Moving Time"]),
        "total_elevation_gain": _num(row["Elevation Gain"]),
        "average_heartrate": _num(row["Average Heart Rate"]),
        "max_heartrate": _num(row["Max Heart Rate"]),
        "average_watts": _num(row["Average Watts"]),
        "calories": _num(row["Calories"]),
        "workout_type": None,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("zip")
    ap.add_argument("--tz", default="Europe/Rome", help="IANA timezone of the runs (not in export)")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()

    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from app.api.strava_auth import PROVIDER
    from app.core.config import get_settings
    from app.core.db import make_engine
    from app.domain.models import SourceRecord
    from app.ingest.strava_client import StravaClient
    from app.worker.queue import JobQueue
    from app.worker.runner import Runner

    engine = make_engine(get_settings().DATABASE_URL)
    runner = Runner(engine, StravaClient(engine), JobQueue(engine))
    with Session(engine) as s:
        done = set(
            s.scalars(
                select(SourceRecord.external_id).where(
                    SourceRecord.source == PROVIDER, SourceRecord.status == "mapped"
                )
            )
        )
    z = zipfile.ZipFile(args.zip)
    rows = [
        r
        for r in csv.DictReader(io.TextIOWrapper(z.open("activities.csv"), encoding="utf-8"))
        if r["Activity Type"] in RUN_TYPES and r["Activity ID"] not in done
    ][: args.limit]
    ok = failed = 0
    for i, row in enumerate(rows, 1):
        try:
            streams, laps = None, []
            if row["Filename"]:
                try:
                    streams, laps = read_track(z, row["Filename"])
                except Exception as e:  # noqa: BLE001  # odd FIT files: keep the summary only
                    print(f"\nNO STREAM {row['Activity ID']}: {type(e).__name__}", file=sys.stderr)
            summary = summary_from_row(
                row, args.tz, trainer=streams is not None and "latlng" not in streams
            )
            detail = {**summary, "laps": laps} if laps else summary
            runner._store(summary, detail, streams, None)
            ok += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL {row['Activity ID']}: {type(e).__name__}: {e}", file=sys.stderr)
        print(f"\r{i}/{len(rows)}", end="", flush=True)
    print(f"\nimported {ok}, failed {failed}")


if __name__ == "__main__":
    main()
