from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _recreate_tsv(source_column: str) -> None:
    op.execute("DROP INDEX IF EXISTS chunks_tsv_idx")
    op.execute("ALTER TABLE chunks DROP COLUMN IF EXISTS tsv")
    op.execute(
        f"ALTER TABLE chunks ADD COLUMN tsv tsvector "
        f"GENERATED ALWAYS AS (to_tsvector('english'::regconfig, {source_column})) STORED"
    )
    op.execute("CREATE INDEX chunks_tsv_idx ON chunks USING GIN (tsv)")


def upgrade() -> None:
    _recreate_tsv("content")
    op.execute("ALTER TABLE chunks DROP COLUMN IF EXISTS embed_text")


def downgrade() -> None:
    op.execute("ALTER TABLE chunks ADD COLUMN IF NOT EXISTS embed_text TEXT")
    op.execute("UPDATE chunks SET embed_text = content WHERE embed_text IS NULL")
    op.execute("ALTER TABLE chunks ALTER COLUMN embed_text SET NOT NULL")
    _recreate_tsv("embed_text")
