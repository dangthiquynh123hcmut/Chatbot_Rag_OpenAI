from __future__ import annotations

import uuid
from abc import ABC, abstractmethod

from app.models.document_model import Document


class IDocumentRepository(ABC):
    @abstractmethod
    def create(self, filename: str, title: str) -> Document:
        """A new document in status 'processing'."""

    @abstractmethod
    def get(self, document_id: uuid.UUID, lock: bool = False) -> Document | None:
        pass

    @abstractmethod
    def get_with_markdown(self, document_id: uuid.UUID) -> Document | None:
        """The document with its (deferred) markdown column loaded."""

    @abstractmethod
    def list_all(self) -> list[Document]:
        """Newest first."""

    @abstractmethod
    def list_ready(self, document_id: uuid.UUID | None = None) -> list[Document]:
        """Ready documents, oldest first; only `document_id` (whatever its status) if given."""

    @abstractmethod
    def delete(self, document: Document) -> None:
        pass

    @abstractmethod
    def set_status(self, document_id: uuid.UUID, status: str, error: str | None = None) -> None:
        pass

    @abstractmethod
    def mark_ready(
        self,
        document_id: uuid.UUID,
        markdown: str,
        num_sections: int,
        num_chunks: int,
        title: str,
        meta: dict,
    ) -> None:
        pass

    @abstractmethod
    def update_meta(self, document_id: uuid.UUID, meta: dict, title: str) -> None:
        pass

    @abstractmethod
    def ready_ids_by_filename(self) -> dict[str, uuid.UUID]:
        """Newest ready document of each filename."""

    @abstractmethod
    def ready_catalog(self) -> list[tuple[uuid.UUID, str, dict]]:
        """(id, title, meta) of every ready document."""
