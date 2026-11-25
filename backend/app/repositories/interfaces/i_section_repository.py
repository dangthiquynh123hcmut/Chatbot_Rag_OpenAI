from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from collections.abc import Mapping


class ISectionRepository(ABC):
    @abstractmethod
    def tree(self, document_id: uuid.UUID) -> list[dict]:
        """Every section but the document root, in reading order:
        id, parent_id, depth, kind, name (last path element), title, refs."""

    @abstractmethod
    def detail(self, section_id: uuid.UUID) -> dict | None:
        """id, document_id, document_title, kind, title, path, refs, full_md."""

    @abstractmethod
    def delete_for_document(self, document_id: uuid.UUID) -> None:
        pass

    @abstractmethod
    def bulk_insert(self, rows: list[dict]) -> None:
        """Insert sections given as column dicts, parents before their children."""

    @abstractmethod
    def subtree(self, section_id: uuid.UUID) -> list[dict]:
        """The section and all its descendants in reading order: id, parent_id, depth, kind, path, full_md, ord."""

    @abstractmethod
    def lookup_by_refs(
        self, refs: Mapping[str, str], document_ids: list[uuid.UUID] | None, limit: int
    ) -> list[dict]:
        """Sections of ready documents whose refs contain `refs`, shallowest first."""

    @abstractmethod
    def titled_sections(self, document_id: uuid.UUID) -> list[dict]:
        """id, title, path of the titled sections (6+ chars) that are not documents, clauses or points."""
