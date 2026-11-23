from __future__ import annotations

import logging
import uuid
from pathlib import Path

from psycopg.types.json import Jsonb

from .config import get_settings
from .converters import convert_to_markdown
from .db import connection
from .hierarchy import build_chunks, embedding_text, parse_markdown, tree_to_markdown
from .llm import embed_texts
from .metadata import DocMeta, display_label, extract_metadata

log = logging.getLogger(__name__)


def create_document(filename: str) -> uuid.UUID:
    doc_id = uuid.uuid4()
    title = Path(filename).stem.replace("_", " ").strip()
    with connection() as conn:
        conn.execute(
            "INSERT INTO documents (id, filename, file_type, title, status) VALUES (%s, %s, %s, %s, 'processing')",
            (doc_id, filename, Path(filename).suffix.lower().lstrip("."), title),
        )
    return doc_id


def _set_status(doc_id: uuid.UUID, status: str, error: str | None = None) -> None:
    with connection() as conn:
        conn.execute("UPDATE documents SET status=%s, error=%s WHERE id=%s", (status, error, doc_id))


def process_document(doc_id: uuid.UUID, path: Path) -> None:
    settings = get_settings()
    try:
        with connection() as conn:
            row = conn.execute("SELECT title, filename, meta FROM documents WHERE id=%s", (doc_id,)).fetchone()
        if row is None:
            return

        raw_md = convert_to_markdown(path)
        meta = DocMeta.from_dict(row["meta"])
        if meta.source != "manual":
            meta = extract_metadata(path, raw_md, row["filename"], use_llm=settings.metadata_llm)
        title = meta.title or row["title"]
        label = display_label(meta, title)
        root = parse_markdown(raw_md, title)
        canonical_md = tree_to_markdown(root)
        (settings.markdown_dir / f"{doc_id}.md").write_text(canonical_md, encoding="utf-8")

        chunks = build_chunks(root, settings.chunk_max_chars, settings.chunk_overlap_paragraphs)
        if not chunks:
            raise ValueError("Tài liệu không có nội dung để lập chỉ mục.")
        embed_inputs = [embedding_text(label, c.node, c.content) for c in chunks]
        vectors = embed_texts(embed_inputs)
        embedding_model = f"{settings.llm_provider}:{settings.active_embedding_model}:{settings.embedding_dim}"

        node_ids = {id(n): uuid.uuid4() for n in root.iter()}
        section_rows = []
        for n in root.iter():
            section_rows.append(
                (
                    node_ids[id(n)],
                    doc_id,
                    node_ids[id(n.parent)] if n.parent is not None else None,
                    n.order,
                    n.depth,
                    n.kind,
                    n.number,
                    n.label if n.kind != "document" else "Phần mở đầu",
                    n.title,
                    n.heading,
                    n.path if n.kind != "document" else ["Phần mở đầu"],
                    Jsonb(n.refs),
                    n.own_text,
                    n.full_markdown() if n.kind != "document" else n.own_text,
                )
            )
        chunk_rows = [
            (uuid.uuid4(), doc_id, node_ids[id(c.node)], c.part, c.content, et, v, embedding_model)
            for c, et, v in zip(chunks, embed_inputs, vectors)
        ]

        with connection() as conn:
            with conn.transaction():
                conn.execute("DELETE FROM sections WHERE document_id=%s", (doc_id,))
                with conn.cursor() as cur:
                    cur.executemany(
                        """INSERT INTO sections (id, document_id, parent_id, ord, depth, kind, number, label,
                           title, heading, path, refs, content_md, full_md)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        section_rows,
                    )
                    cur.executemany(
                        """INSERT INTO chunks (id, document_id, section_id, part, content, embed_text, embedding,
                           embedding_model) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                        chunk_rows,
                    )
                conn.execute(
                    """UPDATE documents SET status='ready', error=NULL, markdown=%s,
                       num_sections=%s, num_chunks=%s, title=%s, meta=%s WHERE id=%s""",
                    (canonical_md, len(section_rows) - 1, len(chunk_rows), title, Jsonb(meta.to_dict()), doc_id),
                )
        log.info("Ingested %s: %d sections, %d chunks", path.name, len(section_rows) - 1, len(chunk_rows))
    except Exception as exc:
        log.exception("Ingest failed for %s", path)
        _set_status(doc_id, "error", str(exc)[:2000])


def refresh_metadata(doc_id: uuid.UUID, stored_file: Path | None, force: bool = False) -> DocMeta | None:
    with connection() as conn:
        row = conn.execute("SELECT filename, title, markdown, meta FROM documents WHERE id=%s", (doc_id,)).fetchone()
    if row is None:
        return None
    current = DocMeta.from_dict(row["meta"])
    if current.source == "manual" and not force:
        return current
    meta = extract_metadata(stored_file, row["markdown"] or "", row["filename"], use_llm=get_settings().metadata_llm)
    with connection() as conn:
        conn.execute(
            "UPDATE documents SET meta=%s, title=%s WHERE id=%s",
            (Jsonb(meta.to_dict()), meta.title or row["title"], doc_id),
        )
    return meta


def ingest_file(path: Path) -> uuid.UUID:
    doc_id = create_document(path.name)
    process_document(doc_id, path)
    return doc_id
