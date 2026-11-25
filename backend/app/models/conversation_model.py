"""Conversations and their messages (chat history)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, CreatedAtMixin, UUIDMixin, timestamp_column


class Conversation(UUIDMixin, CreatedAtMixin, Base):
    __tablename__ = "conversations"
    __table_args__ = (Index("conversations_user_idx", "user_id", text("updated_at DESC")),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = timestamp_column(default="now()")

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", order_by="Message.created_at", passive_deletes=True
    )


class Message(UUIDMixin, Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant')", name="messages_role_check"),
        Index("messages_conversation_idx", "conversation_id", "created_at"),
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Full answer of the RAG pipeline (sources, primary, ...) for assistant messages
    result: Mapped[dict | None] = mapped_column(JSONB)
    document_ids: Mapped[list[uuid.UUID] | None] = mapped_column(ARRAY(UUID(as_uuid=True)))
    # clock_timestamp(): the user and assistant messages of one transaction stay ordered
    created_at: Mapped[datetime] = timestamp_column(default="clock_timestamp()")

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
