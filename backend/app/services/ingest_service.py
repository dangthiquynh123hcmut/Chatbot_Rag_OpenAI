from __future__ import annotations

import logging
import uuid
from pathlib import Path

from app.config import get_settings
from app.converters import convert_to_markdown
from app.core.context import SessionFactory
from app.hierarchy import PREAMBLE, build_chunks, embedding_text, parse_markdown, tree_to_markdown
from app.llm import embed_texts
from app.metadata import DocMeta, display_label, extract_metadata
from app.repositories.chunk_repository import ChunkRepository
from app.repositories.document_repository import DocumentRepository
from app.repositories.section_repository import SectionRepository
from app.services.document_service import default_title
from app.services.interfaces.i_ingest_service import IIngestService
from app.utils.file_utils import markdown_path

log = logging.getLogger(__name__)


class IngestService(IIngestService):
    def __init__(self, session_factory: SessionFactory) -> None:
        # Short sessions: none stays open during conversion / LLM / embedding calls
        self.session_factory = session_factory

    def create_document(self, filename: str) -> uuid.UUID:
        with self.session_factory() as session:
            return DocumentRepository(session).create(filename, default_title(filename)).id

    def ingest_file(self, path: Path) -> uuid.UUID:
        document_id = self.create_document(path.name)
        self.process_document(document_id, path)
        return document_id

    def document_ids(self, document_id: uuid.UUID | None = None) -> list[uuid.UUID]:
        with self.session_factory() as session:
            return [d.id for d in DocumentRepository(session).list_ready(document_id)]

    def process_document(self, document_id: uuid.UUID, path: Path) -> None:
        settings = get_settings()
        try:
            with self.session_factory() as session:
                document = DocumentRepository(session).get(document_id)
                if document is None:
                    return
                stored_title, filename, stored_meta = document.title, document.filename, document.meta

            raw_md = convert_to_markdown(path, str(document_id))
            meta = DocMeta.from_dict(stored_meta)
            if meta.source != "manual":
                meta = extract_metadata(path, raw_md, filename, use_llm=settings.metadata_llm)
            title = meta.title or stored_title
            label = display_label(meta, title)
            root = parse_markdown(raw_md, title)
            canonical_md = tree_to_markdown(root)
            markdown_path(document_id).write_text(canonical_md, encoding="utf-8")

            chunks = build_chunks(root, settings.chunk_max_chars, settings.chunk_overlap_paragraphs)
            if not chunks:
                raise ValueError("Tài liệu không có nội dung để lập chỉ mục.")
            vectors = embed_texts([embedding_text(label, c.node, c.content) for c in chunks])

            node_ids = {id(n): uuid.uuid4() for n in root.iter()}
            section_rows = [
                {
                    "id": node_ids[id(n)],
                    "document_id": document_id,
                    "parent_id": node_ids[id(n.parent)] if n.parent is not None else None,
                    "ord": n.order,
                    "depth": n.depth,
                    "kind": n.kind,
                    "title": n.title,
                    "path": n.path if n.kind != "document" else [PREAMBLE],
                    "refs": n.refs,
                    "full_md": n.full_markdown() if n.kind != "document" else n.own_text,
                }
                for n in root.iter()
            ]
            chunk_rows = [
                {
                    "id": uuid.uuid4(),
                    "document_id": document_id,
                    "section_id": node_ids[id(c.node)],
                    "part": c.part,
                    "content": c.content,
                    "embedding": v,
                }
                for c, v in zip(chunks, vectors)
            ]

            # One transaction: readers see the previous index until it commits
            with self.session_factory() as session:
                sections = SectionRepository(session)
                sections.delete_for_document(document_id)
                sections.bulk_insert(section_rows)
                ChunkRepository(session).bulk_insert(chunk_rows)
                DocumentRepository(session).mark_ready(
                    document_id,
                    markdown=canonical_md,
                    num_sections=len(section_rows) - 1,
                    num_chunks=len(chunk_rows),
                    title=title,
                    meta=meta.to_dict(),
                )
            log.info("Ingested %s: %d sections, %d chunks", path.name, len(section_rows) - 1, len(chunk_rows))
        except Exception as exc:
            log.exception("Ingest failed for %s", path)
            self._set_status(document_id, "error", str(exc)[:2000])

    def refresh_metadata(self, document_id: uuid.UUID, stored_file: Path | None, force: bool = False) -> DocMeta | None:
        with self.session_factory() as session:
            document = DocumentRepository(session).get_with_markdown(document_id)
            if document is None:
                return None
            filename, title, markdown = document.filename, document.title, document.markdown
            current = DocMeta.from_dict(document.meta)
        if current.source == "manual" and not force:
            return current
        meta = extract_metadata(stored_file, markdown or "", filename, use_llm=get_settings().metadata_llm)
        with self.session_factory() as session:
            DocumentRepository(session).update_meta(document_id, meta.to_dict(), meta.title or title)
        return meta

    def _set_status(self, document_id: uuid.UUID, status: str, error: str | None = None) -> None:
        with self.session_factory() as session:
            DocumentRepository(session).set_status(document_id, status, error)
