from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import get_settings

_pool: ConnectionPool | None = None
ALEMBIC_INI = Path(__file__).resolve().parent.parent / "alembic.ini"


def alembic_config():
    from alembic.config import Config

    cfg = Config(str(ALEMBIC_INI))
    cfg.attributes["from_app"] = True
    return cfg


def migrate(revision: str = "head") -> None:
    from alembic import command

    logging.getLogger("alembic.runtime.plugins").setLevel(logging.WARNING)
    command.upgrade(alembic_config(), revision)


def init_db() -> None:
    settings = get_settings()
    migrate()
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        row = conn.execute(
            """SELECT a.atttypmod FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
               WHERE c.relname = 'chunks' AND a.attname = 'embedding'"""
        ).fetchone()
        if row and row[0] > 0 and row[0] != settings.embedding_dim:
            raise RuntimeError(
                f"Bảng chunks đang dùng vector({row[0]}) nhưng EMBEDDING_DIM={settings.embedding_dim}. "
                "Khi đổi provider/model embedding cần tạo lại DB: docker compose down -v; docker compose up -d db "
                "(hoặc viết một migration Alembic đổi kiểu cột rồi nạp lại tài liệu)"
            )


def _configure(conn: psycopg.Connection) -> None:
    register_vector(conn)


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            get_settings().database_url,
            min_size=1,
            max_size=10,
            configure=_configure,
            kwargs={"row_factory": dict_row},
            open=True,
        )
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    with get_pool().connection() as conn:
        yield conn
