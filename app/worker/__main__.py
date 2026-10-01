"""Worker process: `python -m app.worker`."""

import signal
import threading

from app.core.config import get_settings
from app.core.db import make_engine
from app.ingest.strava_client import StravaClient
from app.worker.queue import JobQueue
from app.worker.runner import Runner, Scheduler

POLL_S = 5


def main() -> None:
    engine = make_engine(get_settings().DATABASE_URL)
    queue = JobQueue(engine)
    runner = Runner(engine, StravaClient(engine), queue)
    scheduler = Scheduler(engine, queue)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())

    queue.recover_stale_jobs()
    while not stop.is_set():
        scheduler.tick()
        if job := queue.claim_job():
            runner.execute(job)  # heartbeats go out with each per-activity progress tick
        else:
            stop.wait(POLL_S)


if __name__ == "__main__":
    main()
