from __future__ import annotations

import uuid

from app.candidates import SearchBackend
from app.rag import answer_query
from app.services.interfaces.i_query_service import IQueryService


class QueryService(IQueryService):
    def __init__(self, backend: SearchBackend) -> None:
        self.backend = backend

    def answer(self, query: str, document_ids: list[uuid.UUID] | None = None, top_k: int | None = None) -> dict:
        return answer_query(query, document_ids, top_k, self.backend)
