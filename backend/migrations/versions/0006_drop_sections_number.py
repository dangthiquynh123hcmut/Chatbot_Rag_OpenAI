from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE sections DROP COLUMN IF EXISTS number")


def downgrade() -> None:
    op.execute("ALTER TABLE sections ADD COLUMN IF NOT EXISTS number TEXT")
    op.execute("UPDATE sections SET number = refs ->> kind WHERE number IS NULL AND refs ? kind")
