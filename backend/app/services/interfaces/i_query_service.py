from __future__ import annotations

import uuid
from abc import ABC, abstractmethod


class IQueryService(ABC):
    @abstractmethod
    def answer(self, query: str, document_ids: list[uuid.UUID] | None = None, top_k: int | None = None) -> dict:
        """Retrieve the sections answering `query` and generate the answer (rag.answer_query result)."""
