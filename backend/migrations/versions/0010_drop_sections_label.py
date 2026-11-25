from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# The display label duplicated the last element of `path` (Node.display()); the tree API now returns that instead.
def upgrade() -> None:
    op.execute("ALTER TABLE sections DROP COLUMN IF EXISTS label")


def downgrade() -> None:
    op.execute("ALTER TABLE sections ADD COLUMN IF NOT EXISTS label TEXT")
