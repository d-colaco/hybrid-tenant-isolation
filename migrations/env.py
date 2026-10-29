"""Alembic for two databases: ``alembic -x db=shared upgrade head`` and ``-x db=heavy``. The shared database gets
everything; the heavy one only the ledger lines table and its policies (revisions say which by calling ``is_shared()``)."""

from alembic import context
from sqlalchemy import create_engine

from dclabaccount.config import Settings

target = context.get_x_argument(as_dictionary=True).get("db", "shared")
settings = Settings.load()
url = settings.admin_url(target).replace("postgresql://", "postgresql+psycopg://", 1)


def is_shared() -> bool:
    return target == "shared"


context.config.attributes["is_shared"] = is_shared

engine = create_engine(url)
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=None, version_table="alembic_version")
    with context.begin_transaction():
        context.run_migrations()
