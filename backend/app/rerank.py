from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import TYPE_CHECKING

from .config import get_settings
from .llm import chat_json

if TYPE_CHECKING:
    from .candidates import Candidate

log = logging.getLogger(__name__)

LLM_RERANK_SYSTEM = """Bạn là bộ xếp hạng (reranker) cho hệ thống tra cứu tài liệu tiêu chuẩn, văn bản luật.
Với mỗi đoạn ứng viên, chấm điểm từ 0 đến 10 mức độ đoạn đó CHỨA TRỰC TIẾP thông tin trả lời câu hỏi.
- 10: đoạn chính là mục/điều/khoản trả lời chính xác câu hỏi.
- 5: liên quan nhưng chưa trả lời trực tiếp.
- 0: không liên quan.
Ưu tiên đúng vị trí (chương, mục, điều, khoản) nếu câu hỏi nhắc tới.
Trả về JSON: {"scores": [{"id": <số thứ tự>, "score": <0-10>}]} cho TẤT CẢ ứng viên."""


def _llm_rerank(query: str, cands: list[Candidate]) -> list[float]:
    blocks = []
    for i, c in enumerate(cands):
        text = c.content if len(c.content) <= 1500 else c.content[:1500] + " …"
        blocks.append(f"[{i}] Tài liệu: {c.document_title}\nVị trí: {' > '.join(c.path)}\n{text}")
    user = f"Câu hỏi: {query}\n\nỨng viên:\n\n" + "\n\n---\n\n".join(blocks)
    data = json.loads(chat_json(LLM_RERANK_SYSTEM, user))
    scores = [0.0] * len(cands)
    for item in data.get("scores", []):
        try:
            idx = int(item["id"])
            if 0 <= idx < len(cands):
                scores[idx] = float(item["score"])
        except (KeyError, TypeError, ValueError):
            continue
    return scores


@lru_cache
def _cross_encoder():
    from sentence_transformers import CrossEncoder

    return CrossEncoder(get_settings().cross_encoder_model, max_length=512)


def _cross_encoder_rerank(query: str, cands: list[Candidate]) -> list[float]:
    model = _cross_encoder()
    pairs = [(query, f"{' > '.join(c.path)}\n{c.content}") for c in cands]
    return [float(s) for s in model.predict(pairs)]


def _bedrock_rerank(query: str, cands: list[Candidate]) -> list[float]:
    from .aws import bedrock_agent_runtime, rerank_model_arn

    sources = [
        {
            "type": "INLINE",
            "inlineDocumentSource": {
                "type": "TEXT",
                "textDocument": {"text": f"{' > '.join(c.path)}\n{c.content}"[:4000]},
            },
        }
        for c in cands
    ]
    resp = bedrock_agent_runtime().rerank(
        queries=[{"type": "TEXT", "textQuery": {"text": query}}],
        sources=sources,
        rerankingConfiguration={
            "type": "BEDROCK_RERANKING_MODEL",
            "bedrockRerankingConfiguration": {
                "modelConfiguration": {"modelArn": rerank_model_arn()},
                "numberOfResults": len(cands),
            },
        },
    )
    scores = [0.0] * len(cands)
    for r in resp.get("results", []):
        idx = r.get("index")
        if isinstance(idx, int) and 0 <= idx < len(cands):
            scores[idx] = round(float(r.get("relevanceScore", 0.0)) * 10, 3)
    return scores


def rerank(query: str, cands: list[Candidate], mode: str | None = None) -> list[Candidate]:
    if not cands:
        return cands
    mode = (mode or get_settings().reranker).lower()
    try:
        if mode == "llm":
            scores = _llm_rerank(query, cands)
        elif mode == "bedrock":
            scores = _bedrock_rerank(query, cands)
        elif mode == "cross_encoder":
            scores = _cross_encoder_rerank(query, cands)
        else:
            return cands
    except Exception:
        log.exception("Rerank failed, falling back to fused order")
        return cands
    for c, s in zip(cands, scores):
        c.rerank_score = s
    return sorted(cands, key=lambda c: (c.rerank_score or 0.0, c.fused_score), reverse=True)
