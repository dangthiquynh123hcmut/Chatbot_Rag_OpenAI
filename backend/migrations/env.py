from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool, text

from app.core.database import sqlalchemy_url
from app.models import Base

config = context.config

if config.config_file_name is not None and not config.attributes.get("from_app"):
    fileConfig(config.config_file_name)

MIGRATION_LOCK_ID = 7_351_920_412
target_metadata = Base.metadata
MANUAL_OBJECTS = {
    ("index", "chunks_embedding_idx"),
    ("index", "chunks_tsv_idx"),
    ("index", "sections_refs_idx"),
    ("index", "conversations_user_idx"),
    ("column", "tsv"),
}


def include_object(obj, name, type_, reflected, compare_to):
    if (type_, name) in MANUAL_OBJECTS:
        return False
    if type_ == "table" and name == "alembic_version":
        return False
    return True


def _configure(**kwargs) -> None:
    context.configure(
        target_metadata=target_metadata,
        include_object=include_object,
        compare_type=True,
        transactional_ddl=True,
        **kwargs,
    )


def run_migrations_offline() -> None:
    _configure(url=sqlalchemy_url(), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(sqlalchemy_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        _configure(connection=connection)
        with context.begin_transaction():
            connection.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": MIGRATION_LOCK_ID})
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
