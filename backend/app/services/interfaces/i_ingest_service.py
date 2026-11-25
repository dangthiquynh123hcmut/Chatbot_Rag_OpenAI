from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from pathlib import Path

from app.metadata import DocMeta


class IIngestService(ABC):
    """Indexing pipeline. Runs outside requests (background task, CLI): opens its own sessions."""

    @abstractmethod
    def create_document(self, filename: str) -> uuid.UUID:
        pass

    @abstractmethod
    def process_document(self, document_id: uuid.UUID, path: Path) -> None:
        """Convert, split into sections and chunks, embed and store; the document ends 'ready' or 'error'."""

    @abstractmethod
    def ingest_file(self, path: Path) -> uuid.UUID:
        pass

    @abstractmethod
    def refresh_metadata(self, document_id: uuid.UUID, stored_file: Path | None, force: bool = False) -> DocMeta | None:
        """Extract the metadata again without re-embedding; manual metadata is kept unless `force`."""

    @abstractmethod
    def document_ids(self, document_id: uuid.UUID | None = None) -> list[uuid.UUID]:
        """`document_id` if it exists, else every ready document (oldest first)."""
