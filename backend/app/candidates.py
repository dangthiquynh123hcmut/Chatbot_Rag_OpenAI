from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Protocol, Sequence

from .references import STOPWORDS

RRF_K = 60


@dataclass
class Candidate:
    chunk_id: uuid.UUID | None
    section_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    path: list[str]
    heading: str
    content: str
    full_md: str
    match_type: str
    fused_score: float = 0.0
    vector_score: float | None = None
    keyword_score: float | None = None
    rerank_score: float | None = None
    document_filename: str = ""


class SearchBackend(Protocol):
    def document_ids_by_filename(self) -> dict[str, uuid.UUID]: ...

    def document_catalog(self) -> list: ...

    def lookup_by_references(
        self, refs: dict[str, str], document_ids: list[uuid.UUID] | None, limit: int = 5
    ) -> list[Candidate]: ...

    def vector_search(self, query_vec, document_ids: list[uuid.UUID] | None, k: int) -> list[Candidate]: ...

    def keyword_search(self, query: str, document_ids: list[uuid.UUID] | None, k: int) -> list[Candidate]: ...

    def fetch_subtree(self, section_id: uuid.UUID) -> list[dict]: ...


def keyword_terms(query: str, limit: int = 30) -> list[str]:
    uniq: list[str] = []
    for t in re.findall(r"\w+", query.lower()):
        if len(t) < 2 or t in STOPWORDS or t.isdigit() or "_" in t:
            continue
        if t not in uniq:
            uniq.append(t)
    return uniq[:limit]


def rrf_fuse(vector_hits: Sequence[Candidate], keyword_hits: Sequence[Candidate]) -> list[Candidate]:
    fused: dict = {}
    for kind, hits in (("vector", vector_hits), ("keyword", keyword_hits)):
        for rank, hit in enumerate(hits):
            key = hit.chunk_id or hit.section_id
            cand = fused.get(key)
            if cand is None:
                cand = Candidate(
                    chunk_id=hit.chunk_id,
                    section_id=hit.section_id,
                    document_id=hit.document_id,
                    document_title=hit.document_title,
                    path=hit.path,
                    heading=hit.heading,
                    content=hit.content,
                    full_md=hit.full_md,
                    match_type="semantic",
                    document_filename=hit.document_filename,
                )
                fused[key] = cand
            cand.fused_score += 1.0 / (RRF_K + rank + 1)
            if kind == "vector":
                cand.vector_score = hit.vector_score
            else:
                cand.keyword_score = hit.keyword_score
    return sorted(fused.values(), key=lambda c: c.fused_score, reverse=True)


def dedupe_sections(cands: Sequence[Candidate], limit: int) -> list[Candidate]:
    seen: set = set()
    out: list[Candidate] = []
    for c in cands:
        if c.section_id in seen:
            continue
        seen.add(c.section_id)
        out.append(c)
        if len(out) >= limit:
            break
    return out
