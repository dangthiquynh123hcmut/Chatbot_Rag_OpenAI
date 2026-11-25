"""Documents and their index: the section tree and the embedded chunks."""

from __future__ import annotations

import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import Computed, ForeignKey, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.config import get_settings
from app.models.base import Base, CreatedAtMixin, UUIDMixin

# Must match the config of the generated chunks.tsv column (migration 0002)
TSV_CONFIG = "english"
EMBEDDING_DIM = int(get_settings().embedding_dim)


class Document(UUIDMixin, CreatedAtMixin, Base):
    __tablename__ = "documents"

    filename: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    # processing | ready | error
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'processing'"))
    error: Mapped[str | None] = mapped_column(Text)
    markdown: Mapped[str | None] = mapped_column(Text, deferred=True)
    num_sections: Mapped[int | None] = mapped_column(Integer, server_default=text("0"))
    num_chunks: Mapped[int | None] = mapped_column(Integer, server_default=text("0"))
    # DocMeta.to_dict()
    meta: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))


class Section(UUIDMixin, Base):
    __tablename__ = "sections"
    __table_args__ = (
        Index("sections_doc_idx", "document_id", "ord"),
        Index("sections_refs_idx", "refs", postgresql_using="gin", postgresql_ops={"refs": "jsonb_path_ops"}),
        Index("sections_parent_idx", "parent_id"),
    )

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


class Chunk(UUIDMixin, Base):
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
