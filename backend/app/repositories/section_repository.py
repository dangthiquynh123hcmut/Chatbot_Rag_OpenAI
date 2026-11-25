from __future__ import annotations

import uuid
from collections.abc import Mapping

from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session, aliased

from app.models.document_model import Document, Section
from app.repositories.interfaces.i_section_repository import ISectionRepository

# Section + document columns of a retrieval candidate (services/search_backend.py)
SECTION_COLUMNS = (
    Section.id.label("section_id"),
    Section.document_id,
    Document.title.label("document_title"),
    Document.filename.label("document_filename"),
    Section.path,
    Section.full_md,
    Section.depth,
    Section.kind,
    Section.refs,
)


class SectionRepository(ISectionRepository):
    def __init__(self, session: Session) -> None:
        self.session = session

    def tree(self, document_id: uuid.UUID) -> list[dict]:
        rows = self.session.execute(
            select(
                Section.id,
                Section.parent_id,
                Section.depth,
                Section.kind,
                Section.path[func.cardinality(Section.path)].label("name"),
                Section.title,
                Section.refs,
            )
            .where(Section.document_id == document_id, Section.kind != "document")
            .order_by(Section.ord)
        )
        return [dict(row) for row in rows.mappings()]

    def detail(self, section_id: uuid.UUID) -> dict | None:
        row = (
            self.session.execute(
                select(
                    Section.id,
                    Section.document_id,
                    Document.title.label("document_title"),
                    Section.kind,
                    Section.title,
                    Section.path,
                    Section.refs,
                    Section.full_md,
                )
                .join(Document, Document.id == Section.document_id)
                .where(Section.id == section_id)
            )
            .mappings()
            .one_or_none()
        )
        return dict(row) if row else None

    def delete_for_document(self, document_id: uuid.UUID) -> None:
        # Chunks go with their sections (ON DELETE CASCADE)
        self.session.execute(delete(Section).where(Section.document_id == document_id))

    def bulk_insert(self, rows: list[dict]) -> None:
        if rows:
            self.session.execute(insert(Section), rows)

    def subtree(self, section_id: uuid.UUID) -> list[dict]:
        columns = ("id", "parent_id", "depth", "kind", "path", "full_md", "ord")
        tree = (
            select(*(getattr(Section, c) for c in columns)).where(Section.id == section_id).cte("tree", recursive=True)
        )
        child = aliased(Section)
        tree = tree.union_all(
            select(*(getattr(child, c) for c in columns)).join(tree, child.parent_id == tree.c.id)
        )
        rows = self.session.execute(select(tree).order_by(tree.c.ord))
        return [dict(row) for row in rows.mappings()]

    def lookup_by_refs(
        self, refs: Mapping[str, str], document_ids: list[uuid.UUID] | None, limit: int
    ) -> list[dict]:
        stmt = (
            select(*SECTION_COLUMNS)
            .join(Document, Document.id == Section.document_id)
            .where(Section.refs.contains(dict(refs)), Document.status == "ready")
            .order_by(Section.depth.asc(), Section.ord.asc())
            .limit(limit)
        )
        if document_ids:
            stmt = stmt.where(Section.document_id.in_(document_ids))
        return [dict(row) for row in self.session.execute(stmt).mappings()]

    def titled_sections(self, document_id: uuid.UUID) -> list[dict]:
        rows = self.session.execute(
            select(Section.id, Section.title, Section.path).where(
                Section.document_id == document_id,
                Section.kind.not_in(("document", "clause", "point")),
                Section.title.is_not(None),
                func.length(Section.title) >= 6,
            )
        )
        return [dict(row) for row in rows.mappings()]
