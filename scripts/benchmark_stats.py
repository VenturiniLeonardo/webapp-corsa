"""M6-02: seed 1,000 synthetic runs into a throwaway DB, assert p95 < 300 ms on the stats endpoints.

    python scripts/benchmark_stats.py

In-process (TestClient): measures handler + SQLite, not network. All-time scope = worst case.
"""

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
for k, v in {
    "STRAVA_CLIENT_ID": "bench",
    "STRAVA_CLIENT_SECRET": "bench",
    "ALLOWED_LOGINS": "bench",
    "DATABASE_URL": "sqlite://",
    "ENV": "dev",
    "AUTH_DEV_LOGIN": "bench",
}.items():
    os.environ.setdefault(k, v)

from fastapi.testclient import TestClient
from seed_demo_data import seed
from sqlalchemy.orm import Session

from app.core.db import get_session, make_engine
from app.domain.models import Base
from app.main import app

N, RUNS, WARMUP, LIMIT_MS = 1000, 200, 5, 300
ENDPOINTS = ["/api/stats/summary?from_date=2000-01-01", "/api/stats/volume?bucket=week"]


def p95(xs: list[float]) -> float:
    return sorted(xs)[int(len(xs) * 0.95) - 1]


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        engine = make_engine(f"sqlite:///{Path(tmp) / 'bench.db'}")
        Base.metadata.create_all(engine)
        t0 = time.perf_counter()
        seed(engine, N)
        print(f"seeded {N} runs in {time.perf_counter() - t0:.1f}s")

        def override():  # type: ignore[no-untyped-def]
            with Session(engine) as s:
                yield s

        app.dependency_overrides[get_session] = override
        client, failed = TestClient(app), False
        for url in ENDPOINTS:
            for _ in range(WARMUP):
                assert client.get(url).status_code == 200, url
            ms = []
            for _ in range(RUNS):
                t = time.perf_counter()
                r = client.get(url)
                ms.append((time.perf_counter() - t) * 1000)
                assert r.status_code == 200, url
            p = p95(ms)
            failed |= p >= LIMIT_MS
            print(
                f"{url:45} p50={sorted(ms)[RUNS // 2]:6.1f}ms p95={p:6.1f}ms {'OK' if p < LIMIT_MS else 'FAIL'}"
            )
        engine.dispose()
        sys.exit(int(failed))


if __name__ == "__main__":
    main()
