"""Database module: declarative Base, engine / session factory, migrations."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

ALEMBIC_INI = Path(__file__).resolve().parent.parent.parent / "alembic.ini"


class Base(DeclarativeBase):
    __mapper_args__ = {"eager_defaults": True}


def sqlalchemy_url(url: str | None = None) -> str:
    """DATABASE_URL (postgresql://...) with the psycopg 3 driver SQLAlchemy needs."""
    url = url or get_settings().database_url
    for prefix in ("postgresql+psycopg://", "postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


class Database:
    """Database connection manager: one engine and session factory per process."""

    def __init__(self, url: str, pool_size: int = 5, max_overflow: int = 5) -> None:
        self._engine: Engine = create_engine(
            sqlalchemy_url(url), pool_size=pool_size, max_overflow=max_overflow, pool_pre_ping=True
        )
        self._session_factory: sessionmaker[Session] = sessionmaker(
            bind=self._engine, autoflush=False, expire_on_commit=False
        )

    @classmethod
    def from_settings(cls) -> Database:
        return cls(get_settings().database_url)

    @property
    def engine(self) -> Engine:
        return self._engine

    def create_session(self) -> Session:
        """A new session; the caller commits / closes it."""
        return self._session_factory()

    @contextmanager
    def session_scope(self) -> Iterator[Session]:
        """A session committed on success, rolled back on error, always closed."""
        session = self.create_session()
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    def close(self) -> None:
        self._engine.dispose()


def alembic_config():
    from alembic.config import Config

    cfg = Config(str(ALEMBIC_INI))
    cfg.attributes["from_app"] = True
    return cfg


def migrate(revision: str = "head") -> None:
    from alembic import command

    logging.getLogger("alembic.runtime.plugins").setLevel(logging.WARNING)
    command.upgrade(alembic_config(), revision)


def init_db(database: Database) -> None:
    """Run the migrations, then check the vector column matches EMBEDDING_DIM."""
    settings = get_settings()
    migrate()
    with database.engine.connect() as conn:
        typmod = conn.execute(
            text(
                """SELECT a.atttypmod FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
                   WHERE c.relname = 'chunks' AND a.attname = 'embedding'"""
            )
        ).scalar()
    if typmod and typmod > 0 and typmod != settings.embedding_dim:
        raise RuntimeError(
            f"Bảng chunks đang dùng vector({typmod}) nhưng EMBEDDING_DIM={settings.embedding_dim}. "
            "Khi đổi provider/model embedding cần tạo lại DB: docker compose down -v; docker compose up -d db "
            "(hoặc viết một migration Alembic đổi kiểu cột rồi nạp lại tài liệu)"
        )
