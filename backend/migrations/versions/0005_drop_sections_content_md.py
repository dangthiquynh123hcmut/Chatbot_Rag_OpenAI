from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE sections DROP COLUMN IF EXISTS content_md")


def downgrade() -> None:
    op.execute("ALTER TABLE sections ADD COLUMN IF NOT EXISTS content_md TEXT")
