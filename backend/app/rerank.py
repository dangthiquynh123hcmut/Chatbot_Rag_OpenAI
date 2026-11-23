from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import TYPE_CHECKING

from .config import get_settings
from .llm import chat_json, with_network_retry

if TYPE_CHECKING:
    from .candidates import Candidate

log = logging.getLogger(__name__)

LLM_RERANK_SYSTEM = """You are a reranker for a lookup system over standards, regulations and technical documents.
For each candidate passage, score from 0 to 10 how DIRECTLY it contains the information that answers the question.
- 10: the passage is exactly the section/article/paragraph that answers the question.
- 5: related but does not answer directly.
- 0: unrelated.
Prefer the exact location (chapter, section, article, paragraph) if the question mentions one.
Return JSON: {"scores": [{"id": <number>, "score": <0-10>}]} for ALL candidates."""


def _llm_rerank(query: str, cands: list[Candidate]) -> list[float]:
    blocks = []
    for i, c in enumerate(cands):
        text = c.content if len(c.content) <= 1500 else c.content[:1500] + " …"
        blocks.append(f"[{i}] Document: {c.document_title}\nLocation: {' > '.join(c.path)}\n{text}")
    user = f"Question: {query}\n\nCandidates:\n\n" + "\n\n---\n\n".join(blocks)
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
    resp = with_network_retry(
        bedrock_agent_runtime().rerank,
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
