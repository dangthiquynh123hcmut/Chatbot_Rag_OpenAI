"""Baseline schema: documents, sections, chunks, users, conversations, messages.

Written idempotently (IF NOT EXISTS) so it also upgrades databases created by the
old `schema_sql()` bootstrap without data loss.

Revision ID: 0001
Revises:
Create Date: 2026-11-23
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from app.config import get_settings

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    dim = int(get_settings().embedding_dim)
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        """
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
        )
        """
    )
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS meta JSONB NOT NULL DEFAULT '{}'::jsonb")
    op.execute(
        """
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
            refs JSONB NOT NULL DEFAULT '{}'::jsonb,
            content_md TEXT,
            full_md TEXT
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS sections_doc_idx ON sections(document_id, ord)")
    op.execute("CREATE INDEX IF NOT EXISTS sections_refs_idx ON sections USING GIN (refs jsonb_path_ops)")
    op.execute("CREATE INDEX IF NOT EXISTS sections_parent_idx ON sections(parent_id)")
    op.execute(
        f"""
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
        )
        """
    )
    op.execute("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS embedding_model TEXT")
    op.execute("CREATE INDEX IF NOT EXISTS chunks_embedding_idx ON chunks USING hnsw (embedding vector_cosine_ops)")
    op.execute("CREATE INDEX IF NOT EXISTS chunks_tsv_idx ON chunks USING GIN (tsv)")
    op.execute("CREATE INDEX IF NOT EXISTS chunks_section_idx ON chunks(section_id)")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id UUID PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            display_name TEXT,
            role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_login_at TIMESTAMPTZ
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS conversations (
            id UUID PRIMARY KEY,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS conversations_user_idx ON conversations(user_id, updated_at DESC)")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id UUID PRIMARY KEY,
            conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
            content TEXT NOT NULL,
            result JSONB,
            document_ids UUID[],
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS messages_conversation_idx ON messages(conversation_id, created_at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS messages, conversations, users, chunks, sections, documents")
