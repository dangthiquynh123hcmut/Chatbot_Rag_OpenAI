"""Switch chunk full-text search from the 'simple' to the 'english' text search config.

'english' stems words and drops stopwords, so "supervises" matches "supervising".
PostgreSQL 16 cannot change a generated column's expression in place, so the column
and its GIN index are recreated; this rewrites the chunks table once.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-23
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _recreate_tsv(config: str) -> None:
    op.execute("DROP INDEX IF EXISTS chunks_tsv_idx")
    op.execute("ALTER TABLE chunks DROP COLUMN IF EXISTS tsv")
    op.execute(
        f"ALTER TABLE chunks ADD COLUMN tsv tsvector "
        f"GENERATED ALWAYS AS (to_tsvector('{config}', embed_text)) STORED"
    )
    op.execute("CREATE INDEX chunks_tsv_idx ON chunks USING GIN (tsv)")


def upgrade() -> None:
    _recreate_tsv("english")


def downgrade() -> None:
    _recreate_tsv("simple")
