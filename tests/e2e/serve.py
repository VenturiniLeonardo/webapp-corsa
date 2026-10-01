"""E2E server: fresh seeded DB (100 runs) + the built SPA from web/dist on 127.0.0.1:8001.

Build the frontend first (`npm run build` in web/).
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIST, DB = ROOT / "web" / "dist", ROOT / "data" / "e2e.db"
sys.path.insert(0, str(ROOT))
if not (DIST / "index.html").exists():
    raise SystemExit(f"{DIST} missing: run `npm run build` in web/")

os.environ.update(
    ALLOWED_LOGINS="e2e",
    AUTH_DEV_LOGIN="e2e",
    ENV="dev",
    DATABASE_URL=f"sqlite:///{DB}",
)
DB.parent.mkdir(exist_ok=True)
for suffix in ("", "-wal", "-shm"):
    Path(f"{DB}{suffix}").unlink(missing_ok=True)

import uvicorn

from app.core.db import get_engine
from app.domain.models import Base
from app.main import SPA, app
from scripts.seed_demo_data import seed

Base.metadata.create_all(get_engine())
seed(get_engine(), 100)
app.routes.pop()  # drop the app/static mount (absent in dev), serve the fresh build instead
app.mount("/", SPA(directory=DIST, html=True))
uvicorn.run(app, host="127.0.0.1", port=8001, log_level="warning")
