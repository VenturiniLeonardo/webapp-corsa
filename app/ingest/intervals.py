"""intervals.icu pull: list runs, download each original file, store via the file importer.

Auth: HTTP basic, user `API_KEY`, password = key from intervals.icu /settings. Athlete "0" = self.
Activities intervals.icu got from Strava are not served by its API (Strava terms): skipped.
"""

import gzip
from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.domain.models import Setting
from app.ingest.files import store_file

SEEN_KEY = "intervals_imported"  # ponytail: id list in one settings row; own table if it gets huge


def _client() -> httpx.Client:
    s = get_settings()
    return httpx.Client(
        base_url=s.INTERVALS_BASE_URL,
        auth=("API_KEY", s.INTERVALS_API_KEY),
        timeout=60,
        follow_redirects=True,
    )


def _download(c: httpx.Client, a: dict[str, Any]) -> tuple[str, bytes]:
    ext = (a.get("file_type") or "").lower()
    if ext in ("fit", "gpx", "tcx"):
        r = c.get(f"/api/v1/activity/{a['id']}/file")
    else:  # no original file (e.g. synced from a device API): intervals.icu-generated FIT
        ext, r = "fit", c.get(f"/api/v1/activity/{a['id']}/fit-file")
    r.raise_for_status()
    data = r.content
    if data[:2] == b"\x1f\x8b":  # original files are served gzipped
        data = gzip.decompress(data)
    return f"{a['id']}.{ext}", data


def sync(
    engine: Engine,
    progress: Callable[[int, int], None] = lambda done, total: None,
    http: httpx.Client | None = None,
) -> str | None:
    """Import every intervals.icu run not imported yet. Returns an error note if some failed."""
    s = get_settings()
    with Session(engine) as db:
        row = db.get(Setting, SEEN_KEY)
        seen = set(row.value if row else [])
    with http or _client() as c:
        r = c.get(
            f"/api/v1/athlete/{s.INTERVALS_ATHLETE_ID}/activities",
            params={"oldest": "2000-01-01"},
        )
        r.raise_for_status()
        todo = [
            a
            for a in r.json()
            if str(a["id"]) not in seen
            and a.get("source") != "STRAVA"
            and "run" in (a.get("type") or "").lower()
        ]
        failed: list[str] = []
        progress(0, len(todo))
        for i, a in enumerate(todo, 1):
            try:
                name, data = _download(c, a)
                store_file(engine, name, data, {"name": a.get("name")})
                seen.add(str(a["id"]))
            except Exception as e:  # noqa: BLE001
                failed.append(f"{a['id']}: {type(e).__name__}: {e}"[:200])
            with Session(engine) as db, db.begin():
                db.merge(Setting(key=SEEN_KEY, value=sorted(seen)))
            progress(i, len(todo))
    return f"{len(failed)} failed; " + " | ".join(failed[:3]) if failed else None
