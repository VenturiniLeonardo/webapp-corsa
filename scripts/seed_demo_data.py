"""Synthetic runs (GPS loop, HR, cadence, gzipped streams) for local dev, E2E and benchmarks.

    python scripts/seed_demo_data.py --db data/demo.db --n 100

Refuses to touch a DB that already has activities. Deterministic for a given --seed.
"""

import argparse
import math
import random
import sys
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.db import make_engine
from app.domain.models import Activity, Base, SourceRecord, Stream
from app.domain.stream_codec import encode_stream
from app.worker.runner import CODEC_VERSION, _cfg, _compute_metrics

TZ = "Europe/Rome"
HOME = (45.4642, 9.19)  # synthetic loop start
DT = 10  # seconds between stream samples
M_PER_DEG = 111_320.0


def _stream(rng: random.Random, dist_m: float, speed_ms: float) -> dict[str, list[float]]:
    n = int(dist_m / speed_ms / DT)
    raw = [max(1.5, rng.gauss(speed_ms, 0.25)) for _ in range(n)]
    k = dist_m / (sum(raw) * DT)  # scale so the track covers exactly dist_m
    speed = [round(v * k, 3) for v in raw]
    t = [i * DT for i in range(n)]
    d = [round(sum(speed[:i]) * DT, 1) for i in range(n)]  # O(n²) but n ≤ ~400
    r = dist_m / (2 * math.pi)  # loop whose circumference is the run distance
    lat, lng = [], []
    for x in d:
        a = 2 * math.pi * x / dist_m
        la = HOME[0] + r * math.sin(a) / M_PER_DEG
        lat.append(round(la, 6))
        lng.append(
            round(HOME[1] + r * (math.cos(a) - 1) / (M_PER_DEG * math.cos(math.radians(la))), 6)
        )
    base_hr = rng.uniform(140, 158)
    return {
        "time": t,
        "distance": d,
        "speed": speed,
        "lat": lat,
        "lng": lng,
        "altitude": [round(120 + 15 * math.sin(x / 800) + x / 500, 1) for x in d],
        "hr": [round(base_hr + 12 * i / n + 4 * (v - speed_ms)) for i, v in enumerate(speed)],
        "cadence": [round(rng.gauss(172, 2)) for _ in range(n)],
    }


def seed(engine: Engine, n: int = 100, rng_seed: int = 42, end: date | None = None) -> None:
    """`n` runs, one per ~2 days counting back from `end` (default today); every 7th is a long run."""
    rng, end = random.Random(rng_seed), end or datetime.now(UTC).date()
    tz = ZoneInfo(TZ)
    with Session(engine) as s, s.begin():
        if s.scalar(select(func.count()).select_from(Activity)):
            raise SystemExit("refusing to seed: activities table is not empty")
        cfg = _cfg(s)
        for i in range(n):
            day = end - timedelta(days=2 * i + rng.randint(0, 1))
            kind = "long" if i % 7 == 0 else "workout" if i % 7 == 3 else "easy"
            dist = {"long": rng.uniform(15e3, 22e3), "workout": rng.uniform(8e3, 12e3)}.get(
                kind, rng.uniform(5e3, 9e3)
            )
            speed = {"long": 2.9, "workout": 3.6}.get(kind, 3.1) * rng.uniform(0.95, 1.05)
            ch = _stream(rng, dist, speed)
            local = datetime(day.year, day.month, day.day, 7, rng.randint(0, 59), tzinfo=tz)
            alt, hr = ch["altitude"], ch["hr"]
            gain = sum(max(0.0, b - a) for a, b in pairwise(alt))
            rec = SourceRecord(source="strava", external_id=f"demo-{i}", status="mapped")
            s.add(rec)
            s.flush()
            act = Activity(
                sport_type="run",
                name=f"{kind.capitalize()} run {i + 1}",
                start_time_utc=local.astimezone(UTC).isoformat(timespec="seconds"),
                timezone=TZ,
                local_date=local.date().isoformat(),
                elapsed_s=ch["time"][-1] + DT,
                moving_s=ch["time"][-1] + DT,
                distance_m=ch["distance"][-1],
                elev_gain_m=round(gain, 1),
                elev_loss_m=round(gain * 0.97, 1),
                avg_hr=round(sum(hr) / len(hr), 1),
                max_hr=max(hr),
                avg_cadence_spm=round(sum(ch["cadence"]) / len(ch["cadence"]), 1),
                has_gps=True,
                has_hr=True,
                has_cadence=True,
                is_indoor=False,
                workout_type=kind,
                primary_source_id=rec.id,
                stream_source_id=rec.id,
            )
            s.add(act)
            s.flush()
            rec.activity_id = act.id
            s.add(
                Stream(
                    source_record_id=rec.id,
                    activity_id=act.id,
                    n_points=len(ch["time"]),
                    channels=",".join(ch),
                    data=encode_stream(ch),
                    codec_version=CODEC_VERSION,
                )
            )
            _compute_metrics(s, act, ch, cfg)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    a.db.parent.mkdir(parents=True, exist_ok=True)
    eng = make_engine(f"sqlite:///{a.db}")
    Base.metadata.create_all(eng)
    seed(eng, a.n, a.seed)
    print(f"seeded {a.n} runs into {a.db}")
