from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    Computed,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TIMESTAMP, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .config import get_settings

TSV_CONFIG = "english"
EMBEDDING_DIM = int(get_settings().embedding_dim)


def _ts(nullable: bool = False, default: str | None = None) -> Mapped:
    return mapped_column(
        TIMESTAMP(timezone=True), nullable=nullable, server_default=text(default) if default else None
    )


class Base(DeclarativeBase):
    __mapper_args__ = {"eager_defaults": True}


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'processing'"))
    error: Mapped[str | None] = mapped_column(Text)
    markdown: Mapped[str | None] = mapped_column(Text, deferred=True)
    num_sections: Mapped[int | None] = mapped_column(Integer, server_default=text("0"))
    num_chunks: Mapped[int | None] = mapped_column(Integer, server_default=text("0"))
    created_at: Mapped[datetime] = _ts(default="now()")
    meta: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "filename": self.filename,
            "title": self.title,
            "status": self.status,
            "error": self.error,
            "num_sections": self.num_sections,
            "num_chunks": self.num_chunks,
            "created_at": self.created_at,
            "meta": self.meta,
        }


class Section(Base):
    __tablename__ = "sections"
    __table_args__ = (
        Index("sections_doc_idx", "document_id", "ord"),
        Index("sections_refs_idx", "refs", postgresql_using="gin", postgresql_ops={"refs": "jsonb_path_ops"}),
        Index("sections_parent_idx", "parent_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sections.id", ondelete="CASCADE")
    )
    ord: Mapped[int] = mapped_column(Integer, nullable=False)
    depth: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    path: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    refs: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    full_md: Mapped[str | None] = mapped_column(Text)


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        Index(
            "chunks_embedding_idx",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("chunks_tsv_idx", "tsv", postgresql_using="gin"),
        Index("chunks_section_idx", "section_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    section_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sections.id", ondelete="CASCADE"), nullable=False
    )
    part: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
    tsv: Mapped[str | None] = mapped_column(
        TSVECTOR, Computed(f"to_tsvector('{TSV_CONFIG}'::regconfig, content)", persisted=True)
    )


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("username", name="users_username_key"),
        CheckConstraint("role IN ('user', 'admin')", name="users_role_check"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(Text, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'user'"))
    created_at: Mapped[datetime] = _ts(default="now()")
    last_login_at: Mapped[datetime | None] = _ts(nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "username": self.username,
            "display_name": self.display_name,
            "role": self.role,
            "created_at": self.created_at,
            "last_login_at": self.last_login_at,
        }


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (Index("auth_sessions_user_idx", "user_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    user_agent: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _ts(default="now()")
    last_used_at: Mapped[datetime] = _ts(default="now()")
    expires_at: Mapped[datetime] = _ts()
    revoked_at: Mapped[datetime | None] = _ts(nullable=True)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    __table_args__ = (Index("refresh_tokens_session_idx", "session_id"),)

    token_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("auth_sessions.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = _ts(default="now()")
    used_at: Mapped[datetime | None] = _ts(nullable=True)


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (Index("conversations_user_idx", "user_id", text("updated_at DESC")),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _ts(default="now()")
    updated_at: Mapped[datetime] = _ts(default="now()")

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", order_by="Message.created_at", passive_deletes=True
    )

    def to_dict(self) -> dict:
        return {"id": self.id, "title": self.title, "created_at": self.created_at, "updated_at": self.updated_at}


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant')", name="messages_role_check"),
        Index("messages_conversation_idx", "conversation_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    result: Mapped[dict | None] = mapped_column(JSONB)
    document_ids: Mapped[list[uuid.UUID] | None] = mapped_column(ARRAY(UUID(as_uuid=True)))
    created_at: Mapped[datetime] = _ts(default="clock_timestamp()")

    conversation: Mapped[Conversation] = relationship(back_populates="messages")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "role": self.role,
            "content": self.content,
            "result": self.result,
            "document_ids": self.document_ids,
            "created_at": self.created_at,
        }


CRUD_TABLES = ("documents", "users", "auth_sessions", "refresh_tokens", "conversations", "messages")
RAW_SQL_TABLES = ("sections", "chunks")
