"""Forecast/climatology scenarios. Never use today's weather as a future forecast.

Hourly provider responses are cached in memory, with retrieval time in the audit.
Historical backtests must supply archived scenarios, not call this live adapter.
"""

import calendar
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from datetime import time as clock_time
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.ingest.weather import ARCHIVE_URL, FORECAST_URL

_cache: dict[tuple[Any, ...], tuple[float, dict[str, Any] | None]] = {}
_lock = threading.Lock()


def _fetch(url: str, params: dict[str, Any], ttl: int) -> dict[str, Any] | None:
    key = (url, *sorted(params.items()))
    with _lock:
        cached = _cache.get(key)
        if cached and time.monotonic() < cached[0]:
            return cached[1]
    result = None
    try:
        with httpx.Client(timeout=4) as client:
            response = client.get(url, params=params)
            response.raise_for_status()
            hourly = response.json()["hourly"]
            if not (
                len(hourly["time"]) == len(hourly["temperature_2m"]) == len(hourly["dew_point_2m"])
            ):
                raise ValueError("Weather channel mismatch")
            result = {"hourly": hourly, "retrieved_at": datetime.now(UTC).isoformat()}
    except (httpx.HTTPError, ValueError, TypeError, KeyError):
        pass
    with _lock:
        if len(_cache) >= 64:
            _cache.clear()
        _cache[key] = (time.monotonic() + (ttl if result else 60), result)
    return result


def _valid(temp: Any, dew: Any) -> bool:
    return (
        isinstance(temp, (int, float))
        and isinstance(dew, (int, float))
        and math.isfinite(temp + dew)
        and dew <= temp
    )


def _params(lat: float, lon: float) -> dict[str, Any]:
    return {
        "latitude": round(lat, 2),
        "longitude": round(lon, 2),
        "hourly": "temperature_2m,dew_point_2m",
        "timeformat": "unixtime",
        "timezone": "GMT",
    }


def _nearest(data: dict[str, Any], instant: datetime) -> tuple[float, float] | None:
    h = data["hourly"]
    candidates = [
        (abs(ts - instant.timestamp()), ts, t, d)
        for ts, t, d in zip(h["time"], h["temperature_2m"], h["dew_point_2m"], strict=True)
        if isinstance(ts, (int, float)) and math.isfinite(ts) and _valid(t, d)
    ]
    if not candidates:
        return None
    diff, _, temp, dew = min(candidates)
    return (float(temp), float(dew)) if diff <= 3600 else None


def _year_samples(
    year: int,
    month: int,
    day: int | None,
    local_mid: datetime,
    lat: float,
    lon: float,
    cutoff: datetime,
) -> tuple[list[tuple[float, float]], str | None]:
    if day is None:
        start, end = date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
    else:
        center = date(year, month, min(day, calendar.monthrange(year, month)[1]))
        start, end = center - timedelta(days=15), center + timedelta(days=15)
    params = {
        **_params(lat, lon),
        "start_date": (start - timedelta(days=1)).isoformat(),
        "end_date": (end + timedelta(days=1)).isoformat(),
        "models": "era5",
    }
    data = _fetch(ARCHIVE_URL, params, 86400)
    if not data:
        return [], None
    nearest: dict[date, tuple[float, float, float, float]] = {}
    h = data["hourly"]
    target_seconds = local_mid.hour * 3600 + local_mid.minute * 60
    for stamp, temp, dew in zip(h["time"], h["temperature_2m"], h["dew_point_2m"], strict=True):
        if (
            not isinstance(stamp, (int, float))
            or not math.isfinite(stamp)
            or not -62135596800 < stamp < 253402214400
            or stamp >= (cutoff - timedelta(days=7)).timestamp()
            or not _valid(temp, dew)
        ):
            continue
        local = datetime.fromtimestamp(stamp, local_mid.tzinfo)
        if not start <= local.date() <= end:
            continue
        diff = abs(local.hour * 3600 + local.minute * 60 - target_seconds)
        row = (float(diff), float(stamp), float(temp), float(dew))
        if diff <= 3600 and (local.date() not in nearest or row[:2] < nearest[local.date()][:2]):
            nearest[local.date()] = row
    return [(row[2], row[3]) for _, row in sorted(nearest.items())], data["retrieved_at"]


def scenarios(
    items: list[dict[str, Any]],
    cutoff: datetime,
    lat: float | None,
    lon: float | None,
    race_date: date | None,
    race_month: int | None,
    start_time: clock_time | None,
    timezone: str,
    enabled: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    empty: list[dict[str, Any]] = [{"samples": [], "status": "unavailable"} for _ in items]
    meta: dict[str, Any] = {
        "status": "no_date",
        "source": "Open-Meteo",
        "race_date": race_date,
        "race_month": race_month,
        "race_time": (start_time or clock_time(9)).isoformat(timespec="minutes"),
        "timezone": timezone,
        "time_assumed": start_time is None,
        "message": "Stime standard. Scegli data o mese per uno scenario meteo di gara.",
    }
    if race_date is None and race_month is None:
        return meta, empty
    if not enabled:
        return {
            **meta,
            "status": "disabled",
            "message": "Attiva il meteo nelle Impostazioni per lo scenario gara.",
        }, empty
    if lat is None or lon is None:
        return {
            **meta,
            "status": "no_location",
            "message": "Scegli una località con la ricerca.",
        }, empty
    tz = ZoneInfo(timezone)
    # A month represents a seasonal scenario, not a forecast for the current date.
    nominal = race_date or date(cutoff.year + 1, race_month or 1, 15)
    naive = datetime.combine(nominal, start_time or clock_time(9))
    start = naive.replace(tzinfo=tz)
    if (
        start.utcoffset() != naive.replace(tzinfo=tz, fold=1).utcoffset()
        or start.astimezone(UTC).astimezone(tz).replace(tzinfo=None) != naive
    ):
        return {
            **meta,
            "status": "unavailable",
            "message": "Orario locale ambiguo o inesistente: scegli un altro orario o il fuso UTC.",
        }, empty
    if race_date and start <= cutoff:
        return {
            **meta,
            "status": "unavailable",
            "message": "L'orario gara è già trascorso: resta disponibile la stima standard.",
        }, empty
    mids = [start + timedelta(seconds=(item["elapsed_s"] or 0) / 2) for item in items]
    use_forecast = (
        race_date is not None and 0 <= (race_date - cutoff.astimezone(tz).date()).days <= 10
    )
    forecast = None
    if use_forecast:
        utc_start, utc_end = min(mids).astimezone(UTC).date(), max(mids).astimezone(UTC).date()
        forecast = _fetch(
            FORECAST_URL,
            {
                **_params(lat, lon),
                "start_date": utc_start.isoformat(),
                "end_date": (utc_end + timedelta(days=1)).isoformat(),
            },
            900,
        )
    results: list[dict[str, Any]] = []
    all_retrievals = set()
    for item, mid in zip(items, mids, strict=True):
        if item["elapsed_s"] is None:
            results.append({"status": "unavailable", "samples": []})
            continue
        point = _nearest(forecast, mid) if forecast else None
        if point:
            results.append(
                {
                    "status": "forecast",
                    "samples": [(point[0], point[1], 1)],
                    "years": 0,
                    "midpoint": mid.isoformat(),
                }
            )
            all_retrievals.add(forecast["retrieved_at"] if forecast else "")
            continue
        # Cache hourly yearly windows once; different race durations select
        # different local hours from the same responses without re-downloading.
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [
                pool.submit(
                    _year_samples,
                    year,
                    mid.month,
                    mid.day if race_date else None,
                    mid,
                    lat,
                    lon,
                    cutoff,
                )
                for year in range(cutoff.year - 1, cutoff.year - 11, -1)
            ]
            yearly = [future.result() for future in futures]
        valid = [(rows, retrieved) for rows, retrieved in yearly if rows]
        samples = (
            [(t, d, 1 / len(rows)) for rows, _ in valid for t, d in rows] if len(valid) >= 5 else []
        )
        results.append(
            {
                "status": "climatology" if samples else "unavailable",
                "samples": samples,
                "years": len(valid),
                "midpoint": mid.isoformat(),
            }
        )
        all_retrievals.update(retrieved for _, retrieved in valid if retrieved)
    statuses = {row["status"] for row in results if row.get("samples")}
    status = next(iter(statuses)) if len(statuses) == 1 else "mixed" if statuses else "unavailable"
    messages = {
        "forecast": "Scenario da previsione meteo; forma atletica attuale, senza crescita futura presunta.",
        "climatology": "Scenario climatologico del periodo, non una previsione del tempo della gara.",
        "mixed": "Fonti meteo miste: verifica il dettaglio di ciascuna distanza.",
        "unavailable": "Meteo gara non disponibile: le stime standard restano utilizzabili.",
    }
    return {
        **meta,
        "status": status,
        "message": messages[status],
        "retrieved_at": sorted(all_retrievals),
        "sample_years": min((r.get("years", 0) for r in results if r.get("samples")), default=0),
    }, results
