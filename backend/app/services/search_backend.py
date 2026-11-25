"""PostgreSQL search backend of the RAG pipeline (candidates.SearchBackend); evaluation/memory.py is the
in-memory one. Each call opens a short session: answering a question runs LLM calls between searches."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from app.candidates import Candidate, keyword_terms
from app.core.context import SessionFactory
from app.doc_resolver import CatalogEntry
from app.metadata import DocMeta
from app.references import STRONG_KEYS, ref_variants
from app.repositories.chunk_repository import ChunkRepository
from app.repositories.document_repository import DocumentRepository
from app.repositories.section_repository import SectionRepository


def _candidate(row: dict, match_type: str, chunk: bool) -> Candidate:
    return Candidate(
        chunk_id=row["chunk_id"] if chunk else None,
        section_id=row["section_id"],
        document_id=row["document_id"],
        document_title=row["document_title"],
        path=row["path"],
        content=row["content"] if chunk else (row["full_md"] or ""),
        full_md=row["full_md"] or "",
        match_type=match_type,
        document_filename=row["document_filename"],
    )


class DbSearchBackend:
    def __init__(self, session_factory: SessionFactory) -> None:
        self.session_factory = session_factory

    def document_ids_by_filename(self) -> dict[str, uuid.UUID]:
        with self.session_factory() as session:
            return DocumentRepository(session).ready_ids_by_filename()

    def document_catalog(self) -> list[CatalogEntry]:
        with self.session_factory() as session:
            rows = DocumentRepository(session).ready_catalog()
        return [CatalogEntry(str(document_id), title, DocMeta.from_dict(meta)) for document_id, title, meta in rows]

    def lookup_by_references(
        self, refs: dict[str, str], document_ids: list[uuid.UUID] | None, limit: int = 5
    ) -> list[Candidate]:
        if not refs or not (set(refs) & STRONG_KEYS):
            return []
        with self.session_factory() as session:
            sections = SectionRepository(session)
            for variant in ref_variants(refs):
                rows = sections.lookup_by_refs(variant, document_ids, limit)
                if rows:
                    # Only the shallowest matches: "Article 5" rather than its clauses
                    min_depth = rows[0]["depth"]
                    return [_candidate(r, "reference", False) for r in rows if r["depth"] == min_depth]
        return []

    def vector_search(self, query_vec: Sequence[float], document_ids: list[uuid.UUID] | None, k: int) -> list[Candidate]:
        with self.session_factory() as session:
            rows = ChunkRepository(session).vector_search(query_vec, document_ids, k)
        out = []
        for r in rows:
            c = _candidate(r, "semantic", True)
            c.vector_score = float(r["score"])
            out.append(c)
        return out

    def keyword_search(self, query: str, document_ids: list[uuid.UUID] | None, k: int) -> list[Candidate]:
        terms = keyword_terms(query)
        if not terms:
            return []
        with self.session_factory() as session:
            rows = ChunkRepository(session).keyword_search(terms, document_ids, k)
        out = []
        for r in rows:
            c = _candidate(r, "semantic", True)
            c.keyword_score = float(r["score"])
            out.append(c)
        return out

    def fetch_subtree(self, section_id: uuid.UUID) -> list[dict]:
        with self.session_factory() as session:
            return SectionRepository(session).subtree(section_id)
