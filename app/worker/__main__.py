"""Worker process: `python -m app.worker`."""

import signal
import threading
import time

from app.core.config import get_settings
from app.core.db import make_engine
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

    queue.recover_stale_jobs()
    next_pull = 0.0
    while not stop.is_set():
        if get_settings().INTERVALS_API_KEY and time.monotonic() >= next_pull:
            queue.enqueue("intervals_sync", {})  # dedups against queued/running
            next_pull = time.monotonic() + INTERVALS_EVERY_S
        if job := queue.claim_job():
            runner.execute(job)  # heartbeats go out with each per-activity progress tick
        else:
            stop.wait(POLL_S)


if __name__ == "__main__":
    main()
