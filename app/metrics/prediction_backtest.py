"""Replay exported v2 snapshots and evaluate held-out race results, without network.

Input: JSON list of {athlete_id, event_date, distance_m, actual_elapsed_s,
standard_eligible, snapshot, product: standard|scenario}. Use snapshots captured
before the event; never reconstruct a forecast using observations after it.
No fitting is performed and no statistical calibration claim is made.
"""

import argparse
import hashlib
import json
import math
import statistics
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.api.prediction_inputs import Profile
from app.metrics.predictions_v2 import (
    VERSION,
    Parameters,
    Reference,
    RunV2,
    apply_weather,
    predict_v2,
)


def replay(snapshot: dict[str, Any]) -> dict[str, Any]:
    if snapshot["model_version"] != VERSION:
        raise ValueError("Unsupported model version")
    today = date.fromisoformat(snapshot["today"])
    runs = [RunV2(**{**r, "day": date.fromisoformat(r["day"])}) for r in snapshot["runs"]]
    refs = [
        Reference(**{**r, "run": RunV2(**{**r["run"], "day": date.fromisoformat(r["run"]["day"])})})
        for r in snapshot["references"]
    ]
    if any(r.day > today for r in runs) or any(r.run.day > today for r in refs):
        raise ValueError("Future activity in snapshot")
    profile = Profile.model_validate(snapshot["inputs"].get("profile", {}))
    result = predict_v2(
        runs,
        refs,
        today,
        [(r.from_date, r.to_date) for r in profile.coverage],
        profile.taper_start,
        Parameters(**snapshot["parameters"]),
    )
    apply_weather(result["items"], snapshot["weather_samples"])
    return result


def evaluate(records: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, float] | None]] = {}
    for record in records:
        snapshot = record["snapshot"]
        available = datetime.fromisoformat(snapshot["available_at"])
        if available.tzinfo is None:
            raise ValueError("Snapshot availability requires a timezone")
        if available.date() >= date.fromisoformat(record["event_date"]):
            raise ValueError("Snapshot was not available before race day")
        if any(
            datetime.fromisoformat(t) > available
            for t in snapshot["weather"].get("retrieved_at", [])
        ):
            raise ValueError("Weather retrieved after snapshot availability")
        if date.fromisoformat(snapshot["today"]) >= date.fromisoformat(record["event_date"]):
            raise ValueError(
                "Snapshot must predate the race; same-day snapshots require a separate timestamp audit"
            )
        actual = record["actual_elapsed_s"]
        if not math.isfinite(actual) or actual <= 0:
            raise ValueError("Invalid ground truth")
        product = record.get("product", "standard")
        if product not in ("standard", "scenario"):
            raise ValueError("Unknown product")
        if product == "scenario":
            weather = snapshot["weather"]
            event = date.fromisoformat(record["event_date"])
            if (
                weather.get("race_date") != event.isoformat()
                and weather.get("race_month") != event.month
            ):
                raise ValueError("Weather scenario does not describe this race date or month")
        if product == "standard" and record.get("standard_eligible") is not True:
            raise ValueError(
                "Standard ground truth requires independently verified flat, temperate conditions"
            )
        athlete = str(record["athlete_id"])
        split = (
            "holdout"
            if int(hashlib.sha256(athlete.encode()).hexdigest()[:8], 16) % 5 == 0
            else "development"
        )
        key = f"{split}/{product}/{record['distance_m']}"
        rows = groups.setdefault(key, [])
        items = replay(snapshot)["items"]
        item = next((i for i in items if i["distance_m"] == record["distance_m"]), None)
        if item is None:
            raise ValueError("Unsupported distance")
        point = item if product == "standard" else item["scenario"]
        if point is None or point["elapsed_s"] is None:
            rows.append(None)
            continue
        rows.append(
            {
                "error": (point["elapsed_s"] - actual) / actual,
                "covered": float(point["elapsed_low_s"] <= actual <= point["elapsed_high_s"]),
                "width": (point["elapsed_high_s"] - point["elapsed_low_s"]) / actual,
            }
        )
    report = {}
    for key, rows in groups.items():
        scored = [r for r in rows if r is not None]
        report[key] = {
            "n": len(rows),
            "predicted": len(scored),
            "abstention": 1 - len(scored) / len(rows),
            "mape": statistics.mean(abs(r["error"]) for r in scored) if scored else None,
            "bias": statistics.mean(r["error"] for r in scored) if scored else None,
            "coverage": statistics.mean(r["covered"] for r in scored) if scored else None,
            "relative_width": statistics.mean(r["width"] for r in scored) if scored else None,
        }
    return {"model_version": VERSION, "calibrated": False, "groups": report}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    args = parser.parse_args()
    print(json.dumps(evaluate(json.loads(args.dataset.read_text(encoding="utf-8"))), indent=2))
