from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool, text

from app.config import get_settings

config = context.config

# Only configure logging when run from the `alembic` CLI; the app keeps its own logging setup.
if config.config_file_name is not None and not config.attributes.get("from_app"):
    fileConfig(config.config_file_name)

MIGRATION_LOCK_ID = 7_351_920_412


def sqlalchemy_url() -> str:
    url = get_settings().database_url
    for prefix in ("postgresql+psycopg://", "postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def run_migrations_offline() -> None:
    context.configure(url=sqlalchemy_url(), literal_binds=True, transactional_ddl=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(sqlalchemy_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, transactional_ddl=True)
        with context.begin_transaction():
            # Serialize concurrent upgrades (several workers/containers starting at once).
            connection.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": MIGRATION_LOCK_ID})
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
