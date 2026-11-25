from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def timestamp_column(nullable: bool = False, default: str | None = None) -> Mapped:
    """TIMESTAMPTZ column, with a server-side default expression (e.g. ``now()``) if given."""
    return mapped_column(
        TIMESTAMP(timezone=True), nullable=nullable, server_default=text(default) if default else None
    )


class UUIDMixin:
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class CreatedAtMixin:
    created_at: Mapped[datetime] = timestamp_column(default="now()")


__all__ = ["Base", "CreatedAtMixin", "UUIDMixin", "timestamp_column"]
