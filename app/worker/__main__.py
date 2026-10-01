"""Worker process: `python -m app.worker`."""

import signal
import threading
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import make_engine
from app.domain.models import ActivityMetrics
from app.metrics.engine import ALGO_VERSION
from app.worker.queue import JobQueue
from app.worker.runner import Runner

POLL_S = 5
INTERVALS_EVERY_S = 30 * 60


def main() -> None:
    engine = make_engine(get_settings().DATABASE_URL)
    queue = JobQueue(engine)
    runner = Runner(engine, queue)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())

    with Session(engine) as s:  # metrics code changed since the last run: recompute everything
        if s.scalar(
            select(ActivityMetrics.activity_id)
            .where(ActivityMetrics.algo_version < ALGO_VERSION)
            .limit(1)
        ):
            queue.enqueue("recompute", {})

    next_pull = 0.0
    while not stop.is_set():
        if get_settings().INTERVALS_API_KEY and time.monotonic() >= next_pull:
            queue.enqueue("intervals_sync", {})  # dedups against queued/running
            next_pull = time.monotonic() + INTERVALS_EVERY_S
        # every loop, not just at startup: a restart within STALE_AFTER of a heartbeat
        # would otherwise leave that job 'running' until the next restart
        queue.recover_stale_jobs()
        if job := queue.claim_job():
            runner.execute(job)  # heartbeats go out with each per-activity progress tick
        else:
            stop.wait(POLL_S)


if __name__ == "__main__":
    main()
