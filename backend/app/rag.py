from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field

from .candidates import Candidate, SearchBackend, dedupe_sections, rrf_fuse
from .config import get_settings
from .hierarchy import nfc
from .llm import chat_json, embed_query
from .references import parse_references
from .rerank import rerank

log = logging.getLogger(__name__)

ANSWER_SYSTEM = """Bạn là trợ lý tra cứu tài liệu tiêu chuẩn và văn bản pháp luật.
Chỉ dùng thông tin trong các NGUỒN được cung cấp, tuyệt đối không dùng kiến thức bên ngoài.
Nhiệm vụ:
1. Xác định nguồn (mục/điều/khoản) chứa chính xác thông tin trả lời câu hỏi.
2. Trích NGUYÊN VĂN đoạn văn bản trả lời (quote) - phải là chuỗi con giữ nguyên từng chữ trong nguồn, không diễn giải, không thêm bớt.
3. Viết câu trả lời ngắn gọn, nêu rõ vị trí (ví dụ: "Theo Khoản 2 Điều 3 ...").
Nếu không nguồn nào chứa câu trả lời, đặt found=false và answer giải thích không tìm thấy.
Đặc biệt: nếu nguồn chỉ có nội dung LIÊN QUAN nhưng không nêu đúng thông tin được hỏi (ví dụ hỏi mức phạt, số liệu, tên quốc gia, phiên bản, thời điểm mà nguồn không ghi), phải đặt found=false, không suy diễn hay trả lời gần đúng.
Trả về JSON: {"found": true|false, "source": <số thứ tự nguồn>, "quote": "<trích nguyên văn>", "answer": "<câu trả lời>"}"""


@dataclass
class RetrievalTrace:
    query: str
    refs: dict[str, str]
    reference_hits: list[Candidate] = field(default_factory=list)
    vector_hits: list[Candidate] = field(default_factory=list)
    keyword_hits: list[Candidate] = field(default_factory=list)
    fused: list[Candidate] = field(default_factory=list)
    reranked: list[Candidate] = field(default_factory=list)
    sections: list[Candidate] = field(default_factory=list)


def default_backend() -> SearchBackend:
    from .retrieval import DbBackend

    return DbBackend()


def normalize_for_match(s: str) -> str:
    s = re.sub(r"[#*_`>|]", " ", nfc(s))
    s = re.sub(r"-{3,}", " ", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def _alnum(s: str) -> str:
    return re.sub(r"[\W_]+", "", nfc(s).lower())


def quote_in(quote: str, text: str) -> bool:
    q = normalize_for_match(quote)
    if not q:
        return False
    if q in normalize_for_match(text):
        return True
    a = _alnum(quote)
    return len(a) >= 12 and a in _alnum(text)


def retrieve(
    query: str,
    document_ids: list[uuid.UUID] | None = None,
    top_k: int | None = None,
    backend: SearchBackend | None = None,
    reranker: str | None = None,
) -> RetrievalTrace:
    settings = get_settings()
    backend = backend or default_backend()
    top_k = top_k or settings.final_top_k
    query = nfc(query).strip()
    trace = RetrievalTrace(query=query, refs=parse_references(query))
    trace.reference_hits = backend.lookup_by_references(trace.refs, document_ids)
    trace.vector_hits = backend.vector_search(embed_query(query), document_ids, settings.vector_top_k)
    trace.keyword_hits = backend.keyword_search(query, document_ids, settings.keyword_top_k)
    trace.fused = rrf_fuse(trace.vector_hits, trace.keyword_hits)
    pool = dedupe_sections(trace.fused, settings.rerank_candidates)
    trace.reranked = rerank(query, pool, reranker)
    trace.sections = dedupe_sections(trace.reference_hits + trace.reranked, top_k)
    return trace


def _most_specific(backend: SearchBackend, section_id: uuid.UUID, quote: str) -> dict | None:
    if not quote.strip():
        return None
    best = None
    for row in backend.fetch_subtree(section_id):
        if quote_in(quote, row["full_md"] or ""):
            if best is None or row["depth"] > best["depth"]:
                best = row
    return best


def _source_dict(c: Candidate, rank: int) -> dict:
    return {
        "rank": rank,
        "section_id": str(c.section_id),
        "document_id": str(c.document_id),
        "document_title": c.document_title,
        "document_filename": c.document_filename,
        "path": c.path,
        "heading": c.heading,
        "content": c.full_md or c.content,
        "matched_chunk": c.content if c.chunk_id else None,
        "match_type": c.match_type,
        "vector_score": c.vector_score,
        "keyword_score": c.keyword_score,
        "fused_score": round(c.fused_score, 5),
        "rerank_score": c.rerank_score,
    }


def generate_answer(trace: RetrievalTrace, backend: SearchBackend | None = None) -> dict:
    backend = backend or default_backend()
    sections = trace.sections
    result: dict = {
        "query": trace.query,
        "references_detected": trace.refs,
        "found": False,
        "answer": "Không tìm thấy thông tin phù hợp trong tài liệu.",
        "primary": None,
        "sources": [_source_dict(c, i + 1) for i, c in enumerate(sections)],
    }
    if not sections:
        return result

    blocks = []
    for i, c in enumerate(sections):
        text = c.full_md or c.content
        if len(text) > 6000:
            text = (c.content if c.chunk_id else text[:6000]) + " …"
        blocks.append(f"[NGUỒN {i}] Tài liệu: {c.document_title}\nVị trí: {' > '.join(c.path)}\n\n{text}")
    user = f"Câu hỏi: {trace.query}\n\n" + "\n\n========\n\n".join(blocks)

    try:
        data = json.loads(chat_json(ANSWER_SYSTEM, user))
    except Exception as exc:
        log.exception("Answer generation failed")
        data = {"found": True, "source": 0, "quote": "", "answer": f"(Không gọi được LLM: {exc})"}

    found = bool(data.get("found"))
    try:
        idx = int(data.get("source", 0))
    except (TypeError, ValueError):
        idx = 0
    idx = idx if 0 <= idx < len(sections) else 0
    quote = (data.get("quote") or "").strip()
    if quote and not quote_in(quote, sections[idx].full_md or sections[idx].content):
        idx = next((i for i, c in enumerate(sections) if quote_in(quote, c.full_md or c.content)), idx)
    chosen = sections[idx]
    quote_verified = quote_in(quote, chosen.full_md or chosen.content)

    exact = _most_specific(backend, chosen.section_id, quote) if quote_verified else None
    primary = {
        "section_id": str(exact["id"]) if exact else str(chosen.section_id),
        "document_id": str(chosen.document_id),
        "document_title": chosen.document_title,
        "document_filename": chosen.document_filename,
        "path": exact["path"] if exact else chosen.path,
        "heading": exact["heading"] if exact else chosen.heading,
        "content": (exact["full_md"] if exact else chosen.full_md) or chosen.content,
        "parent_path": chosen.path,
        "quote": quote,
        "quote_verified": quote_verified,
        "match_type": chosen.match_type,
    }
    accepted = found or chosen.match_type == "reference"
    result.update(found=accepted, answer=data.get("answer") or result["answer"], primary=primary if accepted else None)
    return result


def answer_query(
    query: str,
    document_ids: list[uuid.UUID] | None = None,
    top_k: int | None = None,
    backend: SearchBackend | None = None,
) -> dict:
    backend = backend or default_backend()
    return generate_answer(retrieve(query, document_ids, top_k, backend), backend)
