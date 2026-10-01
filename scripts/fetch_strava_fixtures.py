"""Fetch varied Strava activities + streams as anonymized test fixtures (M0-08 spike)."""

import argparse
import json
import os
import random
import sys
from pathlib import Path
from typing import Any

import httpx

KEYS = "time,distance,latlng,altitude,velocity_smooth,heartrate,cadence,watts,moving"
OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "strava"
RUNS = {"Run", "TrailRun", "VirtualRun"}


def categorize(a: dict[str, Any]) -> list[str]:
    cats = []
    if a["sport_type"] not in RUNS:
        cats.append("non_run")
    elif a["sport_type"] == "TrailRun":
        cats.append("trail")
    elif a.get("trainer") or not a.get("start_latlng"):
        cats.append("indoor")
    elif a.get("has_heartrate"):
        cats.append("gps_hr")
    if a["elapsed_time"] - a["moving_time"] > 120 and a["sport_type"] in RUNS:
        cats.append("pauses")
    return cats


def shift(node: Any, dlat: float, dlng: float) -> Any:
    """Offset every [lat, lng] pair found under latlng-like keys; drop polylines."""
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k in ("polyline", "summary_polyline"):
                out[k] = None
            elif k in ("start_latlng", "end_latlng") and v:
                out[k] = [v[0] + dlat, v[1] + dlng]
            else:
                out[k] = shift(v, dlat, dlng)
        return out
    if isinstance(node, list):
        return [shift(x, dlat, dlng) for x in node]
    return node


def shift_streams(streams: dict[str, Any], dlat: float, dlng: float) -> dict[str, Any]:
    if "latlng" in streams:
        streams["latlng"]["data"] = [[la + dlat, lo + dlng] for la, lo in streams["latlng"]["data"]]
    return streams


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--token", default=os.environ.get("STRAVA_ACCESS_TOKEN"), help="or $STRAVA_ACCESS_TOKEN"
    )
    p.add_argument(
        "--api-base", default=os.environ.get("STRAVA_API_BASE", "https://www.strava.com/api/v3")
    )
    p.add_argument("--max", type=int, default=8, help="max activities (5-10)")
    p.add_argument("--pages", type=int, default=3, help="list pages of 100 to scan")
    p.add_argument("--seed", type=int, default=None, help="RNG seed for the GPS offset")
    a = p.parse_args()
    if not a.token:
        p.error("access token required (--token or STRAVA_ACCESS_TOKEN)")

    rng = random.Random(a.seed)
    dlat, dlng = rng.uniform(-0.5, 0.5), rng.uniform(-0.5, 0.5)  # one fixed offset per run
    OUT.mkdir(parents=True, exist_ok=True)

    with httpx.Client(
        base_url=a.api_base, headers={"Authorization": f"Bearer {a.token}"}, timeout=30
    ) as c:
        picked: dict[int, list[str]] = {}
        seen_cats: set[str] = set()
        for page in range(1, a.pages + 1):
            r = c.get("/athlete/activities", params={"per_page": 100, "page": page})
            r.raise_for_status()
            acts = r.json()
            for act in acts:
                new = set(categorize(act)) - seen_cats
                if new and len(picked) < a.max:
                    picked[act["id"]] = sorted(new)
                    seen_cats |= new
            if not acts or len(picked) >= a.max:
                break
        for aid, cats in picked.items():
            d = c.get(f"/activities/{aid}")
            d.raise_for_status()
            s = c.get(f"/activities/{aid}/streams", params={"keys": KEYS, "key_by_type": "true"})
            s.raise_for_status()
            (OUT / f"{aid}_detail.json").write_text(
                json.dumps(shift(d.json(), dlat, dlng), indent=1)
            )
            (OUT / f"{aid}_streams.json").write_text(
                json.dumps(shift_streams(s.json(), dlat, dlng))
            )
            print(f"{aid}: {','.join(cats)}")
        missing = {"gps_hr", "indoor", "trail", "non_run", "pauses"} - seen_cats
        if missing:
            print(f"not found: {', '.join(sorted(missing))}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
