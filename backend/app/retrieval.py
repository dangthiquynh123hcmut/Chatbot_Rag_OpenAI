from __future__ import annotations

import uuid

from psycopg.types.json import Jsonb

from .candidates import Candidate, keyword_terms
from .db import connection
from .doc_resolver import CatalogEntry
from .metadata import DocMeta
from .references import STRONG_KEYS, ref_variants

SECTION_COLS = """s.id AS section_id, s.document_id, d.title AS document_title, d.filename AS document_filename,
                  s.path, s.heading, s.full_md, s.depth, s.kind, s.refs"""
JOINS = "FROM chunks c JOIN sections s ON s.id = c.section_id JOIN documents d ON d.id = c.document_id"
# Must match the config of the generated chunks.tsv column (migration 0002).
TS_CONFIG = "english"


def _candidate(r: dict, match_type: str, chunk: bool) -> Candidate:
    return Candidate(
        chunk_id=r["chunk_id"] if chunk else None,
        section_id=r["section_id"],
        document_id=r["document_id"],
        document_title=r["document_title"],
        path=r["path"],
        heading=r["heading"] or "",
        content=r["content"] if chunk else (r["full_md"] or ""),
        full_md=r["full_md"] or "",
        match_type=match_type,
        document_filename=r["document_filename"],
    )


class DbBackend:
    def document_ids_by_filename(self) -> dict[str, uuid.UUID]:
        with connection() as conn:
            rows = conn.execute(
                "SELECT DISTINCT ON (filename) filename, id FROM documents WHERE status='ready' "
                "ORDER BY filename, created_at DESC"
            ).fetchall()
        return {r["filename"]: r["id"] for r in rows}

    def document_catalog(self) -> list[CatalogEntry]:
        with connection() as conn:
            rows = conn.execute("SELECT id, title, meta FROM documents WHERE status='ready'").fetchall()
        return [CatalogEntry(str(r["id"]), r["title"], DocMeta.from_dict(r["meta"])) for r in rows]

    def lookup_by_references(
        self, refs: dict[str, str], document_ids: list[uuid.UUID] | None, limit: int = 5
    ) -> list[Candidate]:
        if not refs or not (set(refs) & STRONG_KEYS):
            return []
        doc_filter = "AND s.document_id = ANY(%(docs)s)" if document_ids else ""
        with connection() as conn:
            for variant in ref_variants(refs):
                rows = conn.execute(
                    f"""SELECT {SECTION_COLS} FROM sections s JOIN documents d ON d.id = s.document_id
                        WHERE s.refs @> %(refs)s AND d.status = 'ready' {doc_filter}
                        ORDER BY s.depth ASC, s.ord ASC LIMIT %(limit)s""",
                    {"refs": Jsonb(variant), "docs": document_ids, "limit": limit},
                ).fetchall()
                if rows:
                    min_depth = rows[0]["depth"]
                    return [_candidate(r, "reference", False) for r in rows if r["depth"] == min_depth]
        return []

    def vector_search(self, query_vec, document_ids: list[uuid.UUID] | None, k: int) -> list[Candidate]:
        doc_filter = "AND c.document_id = ANY(%(docs)s)" if document_ids else ""
        with connection() as conn:
            rows = conn.execute(
                f"""SELECT c.id AS chunk_id, c.content, {SECTION_COLS}, 1 - (c.embedding <=> %(vec)s) AS score
                    {JOINS} WHERE d.status = 'ready' {doc_filter}
                    ORDER BY c.embedding <=> %(vec)s LIMIT %(k)s""",
                {"vec": query_vec, "docs": document_ids, "k": k},
            ).fetchall()
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
        doc_filter = "AND c.document_id = ANY(%(docs)s)" if document_ids else ""
        with connection() as conn:
            rows = conn.execute(
                f"""SELECT c.id AS chunk_id, c.content, {SECTION_COLS},
                           ts_rank_cd(c.tsv, to_tsquery('{TS_CONFIG}', %(tsq)s)) AS score
                    {JOINS} WHERE d.status = 'ready' {doc_filter} AND c.tsv @@ to_tsquery('{TS_CONFIG}', %(tsq)s)
                    ORDER BY score DESC LIMIT %(k)s""",
                {"tsq": " | ".join(terms), "docs": document_ids, "k": k},
            ).fetchall()
        out = []
        for r in rows:
            c = _candidate(r, "semantic", True)
            c.keyword_score = float(r["score"])
            out.append(c)
        return out

    def fetch_subtree(self, section_id: uuid.UUID) -> list[dict]:
        return fetch_subtree(section_id)


def fetch_subtree(section_id: uuid.UUID) -> list[dict]:
    with connection() as conn:
        return conn.execute(
            """WITH RECURSIVE tree AS (
                   SELECT * FROM sections WHERE id = %s
                   UNION ALL
                   SELECT s.* FROM sections s JOIN tree t ON s.parent_id = t.id
               )
               SELECT id, parent_id, depth, kind, path, heading, full_md, ord FROM tree ORDER BY ord""",
            (section_id,),
        ).fetchall()
