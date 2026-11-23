from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import get_settings

_pool: ConnectionPool | None = None


def schema_sql(dim: int) -> str:
    return f"""
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY,
    filename TEXT NOT NULL,
    file_type TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'processing',
    error TEXT,
    markdown TEXT,
    num_sections INT DEFAULT 0,
    num_chunks INT DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE documents ADD COLUMN IF NOT EXISTS meta JSONB NOT NULL DEFAULT '{{}}'::jsonb;

CREATE TABLE IF NOT EXISTS sections (
    id UUID PRIMARY KEY,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    parent_id UUID REFERENCES sections(id) ON DELETE CASCADE,
    ord INT NOT NULL,
    depth INT NOT NULL,
    kind TEXT NOT NULL,
    number TEXT,
    label TEXT,
    title TEXT,
    heading TEXT,
    path TEXT[] NOT NULL,
    refs JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    content_md TEXT,
    full_md TEXT
);
CREATE INDEX IF NOT EXISTS sections_doc_idx ON sections(document_id, ord);
CREATE INDEX IF NOT EXISTS sections_refs_idx ON sections USING GIN (refs jsonb_path_ops);
CREATE INDEX IF NOT EXISTS sections_parent_idx ON sections(parent_id);

CREATE TABLE IF NOT EXISTS chunks (
    id UUID PRIMARY KEY,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    section_id UUID NOT NULL REFERENCES sections(id) ON DELETE CASCADE,
    part INT NOT NULL DEFAULT 0,
    content TEXT NOT NULL,
    embed_text TEXT NOT NULL,
    embedding vector({dim}) NOT NULL,
    embedding_model TEXT,
    tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', embed_text)) STORED
);
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS embedding_model TEXT;
CREATE INDEX IF NOT EXISTS chunks_embedding_idx ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS chunks_tsv_idx ON chunks USING GIN (tsv);
CREATE INDEX IF NOT EXISTS chunks_section_idx ON chunks(section_id);

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    display_name TEXT,
    role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS conversations (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS conversations_user_idx ON conversations(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS messages (
    id UUID PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    result JSONB,
    document_ids UUID[],
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS messages_conversation_idx ON messages(conversation_id, created_at);
"""


def init_db() -> None:
    settings = get_settings()
    with psycopg.connect(settings.database_url, autocommit=True) as conn:
        conn.execute(schema_sql(settings.embedding_dim))
        row = conn.execute(
            """SELECT a.atttypmod FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
               WHERE c.relname = 'chunks' AND a.attname = 'embedding'"""
        ).fetchone()
        if row and row[0] > 0 and row[0] != settings.embedding_dim:
            raise RuntimeError(
                f"Bảng chunks đang dùng vector({row[0]}) nhưng EMBEDDING_DIM={settings.embedding_dim}. "
                "Khi đổi provider/model embedding cần tạo lại DB: docker compose down -v; docker compose up -d db"
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
