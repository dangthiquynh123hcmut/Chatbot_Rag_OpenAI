"""Login sessions and rotating refresh tokens.

auth_sessions: one row per login (device). Access tokens carry its id as `sid`, so
revoking the session (logout, password change) invalidates them immediately.
refresh_tokens: SHA-256 hashes of opaque refresh tokens; each is single-use and is
replaced on every refresh (rotation). Reusing an old one revokes the whole session.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-23
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE auth_sessions (
            id UUID PRIMARY KEY,
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            user_agent TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_used_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at TIMESTAMPTZ NOT NULL,
            revoked_at TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE INDEX auth_sessions_user_idx ON auth_sessions(user_id)")
    op.execute(
        """
        CREATE TABLE refresh_tokens (
            token_hash TEXT PRIMARY KEY,
            session_id UUID NOT NULL REFERENCES auth_sessions(id) ON DELETE CASCADE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            used_at TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE INDEX refresh_tokens_session_idx ON refresh_tokens(session_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS refresh_tokens, auth_sessions")
