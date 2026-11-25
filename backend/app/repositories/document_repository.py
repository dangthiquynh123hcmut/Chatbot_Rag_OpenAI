from __future__ import annotations

import uuid

from sqlalchemy import select, update
from sqlalchemy.orm import Session, undefer

from app.models.document_model import Document
from app.repositories.interfaces.i_document_repository import IDocumentRepository


class DocumentRepository(IDocumentRepository):
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, filename: str, title: str) -> Document:
        document = Document(filename=filename, title=title, status="processing")
        self.session.add(document)
        self.session.flush()
        return document

    def get(self, document_id: uuid.UUID, lock: bool = False) -> Document | None:
        return self.session.get(Document, document_id, with_for_update=lock)

    def get_with_markdown(self, document_id: uuid.UUID) -> Document | None:
        return self.session.scalar(
            select(Document).options(undefer(Document.markdown)).where(Document.id == document_id)
        )

    def list_all(self) -> list[Document]:
        return list(self.session.scalars(select(Document).order_by(Document.created_at.desc())).all())

    def list_ready(self, document_id: uuid.UUID | None = None) -> list[Document]:
        stmt = select(Document)
        if document_id is not None:
            stmt = stmt.where(Document.id == document_id)
        else:
            stmt = stmt.where(Document.status == "ready").order_by(Document.created_at)
        return list(self.session.scalars(stmt).all())

    def delete(self, document: Document) -> None:
        # Sections and chunks go with it (ON DELETE CASCADE)
        self.session.delete(document)
        self.session.flush()

    def set_status(self, document_id: uuid.UUID, status: str, error: str | None = None) -> None:
        self.session.execute(update(Document).where(Document.id == document_id).values(status=status, error=error))

    def mark_ready(
        self,
        document_id: uuid.UUID,
        markdown: str,
        num_sections: int,
        num_chunks: int,
        title: str,
        meta: dict,
    ) -> None:
        self.session.execute(
            update(Document)
            .where(Document.id == document_id)
            .values(
                status="ready",
                error=None,
                markdown=markdown,
                num_sections=num_sections,
                num_chunks=num_chunks,
                title=title,
                meta=meta,
            )
        )

    def update_meta(self, document_id: uuid.UUID, meta: dict, title: str) -> None:
        self.session.execute(update(Document).where(Document.id == document_id).values(meta=meta, title=title))

    def ready_ids_by_filename(self) -> dict[str, uuid.UUID]:
        rows = self.session.execute(
            select(Document.filename, Document.id)
            .where(Document.status == "ready")
            .distinct(Document.filename)
            .order_by(Document.filename, Document.created_at.desc())
        ).all()
        return {filename: document_id for filename, document_id in rows}

    def ready_catalog(self) -> list[tuple[uuid.UUID, str, dict]]:
        rows = self.session.execute(
            select(Document.id, Document.title, Document.meta).where(Document.status == "ready")
        ).all()
        return [(document_id, title, meta) for document_id, title, meta in rows]
