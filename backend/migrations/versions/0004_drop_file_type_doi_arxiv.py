from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS file_type")
    op.execute("UPDATE documents SET meta = meta - 'doi' - 'arxiv_id' WHERE meta ?| array['doi', 'arxiv_id']")


def downgrade() -> None:
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS file_type TEXT")
    op.execute(
        r"UPDATE documents SET file_type = lower(substring(filename from '\.([^.]+)$')) WHERE file_type IS NULL"
    )
    op.execute("UPDATE documents SET file_type = '' WHERE file_type IS NULL")
    op.execute("ALTER TABLE documents ALTER COLUMN file_type SET NOT NULL")
