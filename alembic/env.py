import os

from alembic import context
from app.core.db import make_engine
from app.domain.models import Base

url = os.environ.get("DATABASE_URL") or context.config.get_main_option("sqlalchemy.url")
assert url

if context.is_offline_mode():
    context.configure(url=url, target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    with make_engine(url).connect() as conn:
        # render_as_batch: SQLite-safe ALTERs in future migrations
        context.configure(connection=conn, target_metadata=Base.metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()
