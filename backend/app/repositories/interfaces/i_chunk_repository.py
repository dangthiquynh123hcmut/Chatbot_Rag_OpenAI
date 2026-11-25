from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from collections.abc import Sequence


class IChunkRepository(ABC):
    @abstractmethod
    def bulk_insert(self, rows: list[dict]) -> None:
        pass

    @abstractmethod
    def vector_search(self, query_vec: Sequence[float], document_ids: list[uuid.UUID] | None, k: int) -> list[dict]:
        """The `k` chunks of ready documents nearest to `query_vec` (cosine), with their section and
        document columns and score = cosine similarity."""

    @abstractmethod
    def keyword_search(self, terms: list[str], document_ids: list[uuid.UUID] | None, k: int) -> list[dict]:
        """The `k` chunks of ready documents matching any of `terms` (full-text), best ts_rank_cd first."""

    @abstractmethod
    def rows_for_audit(self, document_id: uuid.UUID) -> list[dict]:
        """id, content, embedding of each chunk with full_md, path, kind of its section."""

    @abstractmethod
    def nearest_section_ids(self, document_id: uuid.UUID, query_vec: Sequence[float], k: int) -> list[uuid.UUID]:
        """Section ids of the `k` chunks of the document nearest to `query_vec`."""
