"""Accounts and login sessions (one AuthSession per device, rotated refresh tokens)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDMixin, timestamp_column


class User(UUIDMixin, CreatedAtMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("username", name="users_username_key"),
        CheckConstraint("role IN ('user', 'admin')", name="users_role_check"),
    )

    username: Mapped[str] = mapped_column(Text, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'user'"))
    last_login_at: Mapped[datetime | None] = timestamp_column(nullable=True)


class AuthSession(UUIDMixin, CreatedAtMixin, Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (Index("auth_sessions_user_idx", "user_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    user_agent: Mapped[str | None] = mapped_column(Text)
    last_used_at: Mapped[datetime] = timestamp_column(default="now()")
    expires_at: Mapped[datetime] = timestamp_column()
    revoked_at: Mapped[datetime | None] = timestamp_column(nullable=True)


class RefreshToken(CreatedAtMixin, Base):
    __tablename__ = "refresh_tokens"
    __table_args__ = (Index("refresh_tokens_session_idx", "session_id"),)

    # SHA-256 of the opaque token given to the client
    token_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("auth_sessions.id", ondelete="CASCADE"), nullable=False
    )
    used_at: Mapped[datetime | None] = timestamp_column(nullable=True)
