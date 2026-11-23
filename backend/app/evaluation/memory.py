from __future__ import annotations

import hashlib
import logging
import math
import re
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..candidates import Candidate, keyword_terms
from ..config import get_settings
from ..converters import SUPPORTED_EXTENSIONS, convert_to_markdown
from ..doc_resolver import CatalogEntry
from ..hierarchy import Node, build_chunks, embedding_text, parse_markdown, tree_to_markdown
from ..metadata import DocMeta, find_identifiers, pdf_info
from ..llm import embed_texts
from ..references import STRONG_KEYS, ref_variants

log = logging.getLogger(__name__)
BM25_K1, BM25_B = 1.5, 0.75


def document_title(filename: str) -> str:
    return Path(filename).stem.replace("_", " ").strip()


def load_document(path: Path) -> tuple[str, Node]:
    title = document_title(path.name)
    return title, parse_markdown(convert_to_markdown(path), title)


def list_documents(target: Path) -> list[Path]:
    if target.is_file():
        return [target]
    return sorted(p for p in target.rglob("*") if p.suffix.lower() in SUPPORTED_EXTENSIONS)


@dataclass
class MemSection:
    id: uuid.UUID
    document_id: uuid.UUID
    filename: str
    title: str
    node: Node
    path: list[str]
    full_md: str


@dataclass
class MemChunk:
    id: uuid.UUID
    section: MemSection
    content: str
    terms: Counter


class EmbeddingCache:
    def __init__(self, cache_dir: Path | None):
        s = get_settings()
        self.tag = f"{s.llm_provider}:{s.active_embedding_model}:{s.embedding_dim}"
        self.path = None
        self.data: dict[str, np.ndarray] = {}
        if cache_dir:
            cache_dir.mkdir(parents=True, exist_ok=True)
            safe = re.sub(r"[^\w.-]+", "_", self.tag)
            self.path = cache_dir / f"embeddings_{safe}.npz"
            if self.path.exists():
                with np.load(self.path) as z:
                    self.data = dict(zip(z["keys"].tolist(), z["vecs"]))

    def key(self, text: str) -> str:
        return hashlib.sha1(f"{self.tag}\n{text}".encode("utf-8")).hexdigest()

    def embed(self, texts: list[str]) -> list[np.ndarray]:
        keys = [self.key(t) for t in texts]
        missing = [i for i, k in enumerate(keys) if k not in self.data]
        if missing:
            vecs = embed_texts([texts[i] for i in missing])
            for i, v in zip(missing, vecs):
                self.data[keys[i]] = np.asarray(v, dtype=np.float32)
            self.save()
        return [self.data[k] for k in keys]

    def save(self) -> None:
        if self.path and self.data:
            keys = list(self.data)
            np.savez_compressed(self.path, keys=np.array(keys), vecs=np.stack([self.data[k] for k in keys]))


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"\w+", text.lower()) if len(t) >= 2 and not t.isdigit()]


class MemoryBackend:
    def __init__(self):
        self.sections: dict[uuid.UUID, MemSection] = {}
        self.by_node: dict[int, MemSection] = {}
        self.chunks: list[MemChunk] = []
        self.doc_ids: dict[str, uuid.UUID] = {}
        self.catalog: list[CatalogEntry] = []
        self.matrix = np.zeros((0, 1), dtype=np.float32)
        self.df: Counter = Counter()
        self.avg_len = 1.0

    @classmethod
    def from_paths(cls, target: Path, cache_dir: Path | None = None) -> "MemoryBackend":
        s = get_settings()
        be = cls()
        cache = EmbeddingCache(cache_dir)
        texts: list[str] = []
        for path in list_documents(target):
            title, root = load_document(path)
            doc_id = uuid.uuid5(uuid.NAMESPACE_URL, f"file:{path.name}")
            be.doc_ids[path.name] = doc_id
            meta = DocMeta.from_dict({"title": title, **pdf_info(path), **find_identifiers(tree_to_markdown(root)[:20000])})
            be.catalog.append(CatalogEntry(str(doc_id), title, meta))
            for n in root.iter():
                sid = uuid.uuid5(doc_id, f"section:{n.order}")
                is_root = n.kind == "document"
                sec = MemSection(
                    sid, doc_id, path.name, title, n,
                    ["Phần mở đầu"] if is_root else n.path,
                    n.own_text if is_root else n.full_markdown(),
                )
                be.sections[sid] = sec
                be.by_node[id(n)] = sec
            for i, ch in enumerate(build_chunks(root, s.chunk_max_chars, s.chunk_overlap_paragraphs)):
                et = embedding_text(title, ch.node, ch.content)
                be.chunks.append(
                    MemChunk(uuid.uuid5(doc_id, f"chunk:{i}"), be.by_node[id(ch.node)], ch.content, Counter(_tokens(et)))
                )
                texts.append(et)
            log.info("Loaded %s", path.name)
        if texts:
            vecs = np.stack(cache.embed(texts))
            norms = np.linalg.norm(vecs, axis=1, keepdims=True)
            be.matrix = vecs / np.maximum(norms, 1e-12)
        for c in be.chunks:
            be.df.update(set(c.terms))
        be.avg_len = float(np.mean([sum(c.terms.values()) for c in be.chunks])) if be.chunks else 1.0
        return be

    def document_ids_by_filename(self) -> dict[str, uuid.UUID]:
        return dict(self.doc_ids)

    def document_catalog(self) -> list[CatalogEntry]:
        return list(self.catalog)

    def _allowed(self, sec: MemSection, document_ids: list[uuid.UUID] | None) -> bool:
        return not document_ids or sec.document_id in document_ids

    def _cand(self, sec: MemSection, content: str, match_type: str, chunk_id: uuid.UUID | None) -> Candidate:
        return Candidate(
            chunk_id=chunk_id,
            section_id=sec.id,
            document_id=sec.document_id,
            document_title=sec.title,
            path=sec.path,
            heading=sec.node.heading,
            content=content,
            full_md=sec.full_md,
            match_type=match_type,
            document_filename=sec.filename,
        )

    def lookup_by_references(
        self, refs: dict[str, str], document_ids: list[uuid.UUID] | None, limit: int = 5
    ) -> list[Candidate]:
        if not refs or not (set(refs) & STRONG_KEYS):
            return []
        for variant in ref_variants(refs):
            hits = [
                s for s in self.sections.values()
                if s.node.kind != "document" and self._allowed(s, document_ids)
                and all(s.node.refs.get(k) == v for k, v in variant.items())
            ]
            if hits:
                hits.sort(key=lambda s: (s.node.depth, s.node.order))
                depth = hits[0].node.depth
                return [self._cand(s, s.full_md, "reference", None) for s in hits if s.node.depth == depth][:limit]
        return []

    def vector_search(self, query_vec, document_ids: list[uuid.UUID] | None, k: int) -> list[Candidate]:
        if not self.chunks:
            return []
        q = np.asarray(query_vec, dtype=np.float32)
        q = q / max(float(np.linalg.norm(q)), 1e-12)
        scores = self.matrix @ q
        out = []
        for i in np.argsort(-scores):
            ch = self.chunks[int(i)]
            if not self._allowed(ch.section, document_ids):
                continue
            c = self._cand(ch.section, ch.content, "semantic", ch.id)
            c.vector_score = float(scores[int(i)])
            out.append(c)
            if len(out) >= k:
                break
        return out

    def keyword_search(self, query: str, document_ids: list[uuid.UUID] | None, k: int) -> list[Candidate]:
        terms = keyword_terms(query)
        if not terms or not self.chunks:
            return []
        n = len(self.chunks)
        scored = []
        for ch in self.chunks:
            if not self._allowed(ch.section, document_ids):
                continue
            length = sum(ch.terms.values())
            score = 0.0
            for t in terms:
                tf = ch.terms.get(t, 0)
                if not tf:
                    continue
                idf = math.log(1 + (n - self.df[t] + 0.5) / (self.df[t] + 0.5))
                score += idf * tf * (BM25_K1 + 1) / (tf + BM25_K1 * (1 - BM25_B + BM25_B * length / self.avg_len))
            if score > 0:
                scored.append((score, ch))
        scored.sort(key=lambda x: -x[0])
        out = []
        for score, ch in scored[:k]:
            c = self._cand(ch.section, ch.content, "semantic", ch.id)
            c.keyword_score = score
            out.append(c)
        return out

    def fetch_subtree(self, section_id: uuid.UUID) -> list[dict]:
        sec = self.sections[section_id]
        rows = []
        for n in sec.node.iter():
            s = self.by_node[id(n)]
            rows.append({"id": s.id, "depth": n.depth, "kind": n.kind, "path": s.path, "heading": n.heading,
                         "full_md": s.full_md, "ord": n.order})
        return rows
