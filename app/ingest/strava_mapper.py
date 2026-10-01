"""Pure Strava -> canonical mapper (PLAN §8.3, §9.4). No I/O."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MAPPER_VERSION = 1

RUN_SPORTS = {"Run": "run", "TrailRun": "trail_run", "VirtualRun": "treadmill"}
WORKOUT_TYPES = {None: "easy", 0: "easy", 1: "race", 2: "long", 3: "workout"}
STREAM_RENAME = {
    "time": "time",
    "distance": "distance",
    "altitude": "altitude",
    "velocity_smooth": "speed",
    "heartrate": "hr",
    "watts": "power",
    "moving": "moving",
}
CADENCE_FACTOR = 2  # Strava running cadence = one leg (RPM) -> steps/min


@dataclass
class ActivityDraft:
    status: str  # 'mapped' | 'skipped'
    external_id: str
    source_start_time_utc: str | None = None
    mapper_version: int = MAPPER_VERSION
    sport_type: str | None = None
    name: str | None = None
    start_time_utc: str | None = None
    timezone: str | None = None
    local_date: str | None = None
    elapsed_s: int | None = None
    moving_s: int | None = None
    distance_m: float | None = None
    elev_gain_m: float | None = None
    avg_hr: float | None = None
    max_hr: float | None = None
    avg_cadence_spm: float | None = None
    avg_power_w: float | None = None
    calories_kcal: int | None = None
    has_gps: bool = False
    has_hr: bool = False
    has_cadence: bool = False
    is_indoor: bool = False
    workout_type: str | None = None
    summary_polyline: str | None = None


@dataclass
class LapDraft:
    kind: str
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


@dataclass
class StreamDraft:
    channels: dict[str, list[Any]] = field(default_factory=dict)

    @property
    def n_points(self) -> int:
        return len(self.channels.get("time", []))


def _utc(s: str) -> datetime:
    return datetime.fromisoformat(s)  # py3.11+ parses "Z"


def _iana(tz: str | None) -> str | None:
    # "(GMT+01:00) Europe/Rome" -> "Europe/Rome"
    return tz.rsplit(") ", 1)[-1].strip() if tz else None


def _spm(rpm: float | None) -> float | None:
    return rpm * CADENCE_FACTOR if rpm is not None else None


def _opt_float(v: Any) -> float | None:
    return float(v) if v is not None else None


def _opt_int(v: Any) -> int | None:
    return int(v) if v is not None else None


def _map_streams(raw: dict[str, Any]) -> StreamDraft | None:
    # accepts key_by_type shape {"time": {"data": [...]}} or plain {"time": [...]}
    data = {k: (v["data"] if isinstance(v, dict) else v) for k, v in raw.items()}
    ch = {STREAM_RENAME[k]: v for k, v in data.items() if k in STREAM_RENAME}
    if "latlng" in data:
        ch["lat"] = [p[0] if p else None for p in data["latlng"]]
        ch["lng"] = [p[1] if p else None for p in data["latlng"]]
    if "cadence" in data:
        ch["cadence"] = [_spm(c) for c in data["cadence"]]
    return StreamDraft(ch) if ch else None


def map_strava_activity(
    raw_detail: dict[str, Any], raw_streams: dict[str, Any] | None
) -> tuple[ActivityDraft, list[LapDraft], StreamDraft | None]:
    ext_id = str(raw_detail["id"])
    start = _utc(raw_detail["start_date"])
    start_iso = start.isoformat(timespec="seconds")
    sport = raw_detail.get("sport_type") or raw_detail.get("type")
    if sport not in RUN_SPORTS:
        return ActivityDraft("skipped", ext_id, source_start_time_utc=start_iso), [], None

    trainer = bool(raw_detail.get("trainer"))
    is_indoor = sport == "VirtualRun" or trainer
    tz = _iana(raw_detail.get("timezone"))
    try:
        local_date = start.astimezone(ZoneInfo(tz)).date().isoformat() if tz else None
    except (ZoneInfoNotFoundError, ValueError):
        local_date = None
    if local_date is None:  # fallback: Strava's own local wall time
        local_date = (raw_detail.get("start_date_local") or start_iso)[:10]

    stream = _map_streams(raw_streams) if raw_streams else None
    ch = stream.channels if stream else {}
    avg_cad = _spm(raw_detail.get("average_cadence"))

    act = ActivityDraft(
        status="mapped",
        external_id=ext_id,
        source_start_time_utc=start_iso,
        sport_type="treadmill" if trainer else RUN_SPORTS[sport],
        name=raw_detail.get("name"),
        start_time_utc=start_iso,
        timezone=tz,
        local_date=local_date,
        elapsed_s=_opt_int(raw_detail.get("elapsed_time")),
        moving_s=_opt_int(raw_detail.get("moving_time")),
        distance_m=_opt_float(raw_detail.get("distance")),
        elev_gain_m=_opt_float(raw_detail.get("total_elevation_gain")),
        avg_hr=_opt_float(raw_detail.get("average_heartrate")),
        max_hr=_opt_float(raw_detail.get("max_heartrate")),
        avg_cadence_spm=avg_cad,
        avg_power_w=_opt_float(raw_detail.get("average_watts")),
        calories_kcal=_opt_int(raw_detail.get("calories")),
        has_gps="lat" in ch or bool(raw_detail.get("start_latlng")),
        has_hr="hr" in ch or bool(raw_detail.get("has_heartrate")),
        has_cadence="cadence" in ch or avg_cad is not None,
        is_indoor=is_indoor,
        workout_type=WORKOUT_TYPES.get(raw_detail.get("workout_type"), "other"),
        summary_polyline=(raw_detail.get("map") or {}).get("summary_polyline"),
    )

    laps = [
        LapDraft(
            kind="device_lap",
            idx=i,
            start_offset_s=(
                int((_utc(lp["start_date"]) - start).total_seconds())
                if lp.get("start_date")
                else None
            ),
            elapsed_s=_opt_int(lp.get("elapsed_time")),
            moving_s=_opt_int(lp.get("moving_time")),
            distance_m=_opt_float(lp.get("distance")),
            avg_speed_ms=_opt_float(lp.get("average_speed")),
            avg_hr=_opt_float(lp.get("average_heartrate")),
            max_hr=_opt_float(lp.get("max_heartrate")),
            avg_cadence_spm=_spm(lp.get("average_cadence")),
            elev_gain_m=_opt_float(lp.get("total_elevation_gain")),
        )
        for i, lp in enumerate(raw_detail.get("laps") or [])
    ]
    return act, laps, stream
