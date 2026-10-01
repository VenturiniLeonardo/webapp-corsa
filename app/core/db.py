from collections.abc import Iterator
from functools import lru_cache
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


def make_engine(url: str) -> Engine:
    engine = create_engine(url)

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn: Any, _record: Any) -> None:
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


@lru_cache
def get_engine() -> Engine:
    return make_engine(get_settings().DATABASE_URL)


def get_session() -> Iterator[Session]:
    """FastAPI dependency; handlers commit explicitly."""
    with make_session_factory(get_engine())() as s:
        yield s
