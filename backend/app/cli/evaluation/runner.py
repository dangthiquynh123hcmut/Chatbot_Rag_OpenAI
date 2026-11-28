from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from ...candidates import Candidate, SearchBackend
from ...config import get_settings
from ...llm import effective_judge_model, judge_chat_json
from ...rag import generate_answer, retrieve
from ...references import parse_references
from . import metrics as M
from .dataset import EvalItem

log = logging.getLogger(__name__)

KS = (1, 3, 5, 10)
BRANCHES = ("vector", "keyword", "hybrid", "rerank", "pipeline", "reference")
CONTAINS = {"exact", "ancestor"}
EXACT = {"exact"}
CITATION_KEYS = ("chapter", "section", "article", "clause", "point")

JUDGE_SYSTEM = """You are a judge evaluating answers from a document lookup system.
Given the question, the SOURCES the system used, the system's answer and the reference answer (if any).
Score:
- "faithfulness": a float 0..1 = the fraction of claims in the answer directly supported by the SOURCES (1 = fully grounded).
- "correctness": "correct" | "partial" | "incorrect" against the reference answer (if there is none, against the source containing the answer).
- "reason": one short sentence of explanation.
Return JSON: {"faithfulness": 0.0, "correctness": "correct", "reason": "..."}"""


def _ranked(cands: list[Candidate]) -> list[tuple[str, list[str]]]:
    seen, out = set(), []
    for c in cands:
        if c.section_id in seen:
            continue
        seen.add(c.section_id)
        out.append((c.document_filename, c.path))
    return out


def _scope_ids(item: EvalItem, doc_ids: dict, scope: str):
    if scope == "document" and item.document in doc_ids:
        return [doc_ids[item.document]]
    return None


def _parallel(fn: Callable, items: list, workers: int) -> list:
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        return list(pool.map(fn, items))


def run_retrieval(
    items: list[EvalItem], backend: SearchBackend, scope: str = "all", reranker: str | None = None, workers: int = 4
) -> dict:
    doc_ids = backend.document_ids_by_filename()
    answerable = [i for i in items if i.answerable and i.gold_path]

    def one(item: EvalItem) -> dict:
        t0 = time.perf_counter()
        trace = retrieve(item.question, _scope_ids(item, doc_ids, scope), max(KS), backend, reranker)
        branches = {
            "vector": trace.vector_hits,
            "keyword": trace.keyword_hits,
            "hybrid": trace.fused,
            "rerank": trace.reranked,
            "pipeline": trace.sections,
            "reference": trace.reference_hits,
        }
        row = {"id": item.id, "type": item.type, "question": item.question, "document": item.document,
               "gold_path": item.gold_path, "latency_s": round(time.perf_counter() - t0, 3), "refs": trace.refs}
        for name, cands in branches.items():
            ranked = _ranked(cands)
            row[name] = {
                "contains_rank": M.first_rank(ranked, item.document, item.gold_path, CONTAINS),
                "exact_rank": M.first_rank(ranked, item.document, item.gold_path, EXACT),
                "top3": [" > ".join(p) for _, p in ranked[:3]],
            }
        return row

    rows = _parallel(one, answerable, workers)
    summary: dict = {"n": len(rows), "scope": scope, "reranker": reranker or get_settings().reranker, "branches": {}}
    for name in BRANCHES:
        subset = rows if name != "reference" else [r for r in rows if r["refs"]]
        if not subset:
            continue
        contains = [r[name]["contains_rank"] for r in subset]
        exact = [r[name]["exact_rank"] for r in subset]
        entry = {"n": len(subset), **{f"recall@{k}": round(M.recall_at(contains, k), 4) for k in KS},
                 **{f"exact@{k}": round(M.recall_at(exact, k), 4) for k in KS},
                 "mrr@10": round(M.mrr(contains), 4), "ndcg@10": round(M.ndcg(contains), 4)}
        by_type = defaultdict(list)
        for r in subset:
            by_type[r["type"]].append(r[name]["contains_rank"])
        entry["recall@5_by_type"] = {t: round(M.recall_at(v, 5), 4) for t, v in sorted(by_type.items())}
        summary["branches"][name] = entry
    summary["latency_p50_s"] = round(M.percentile([r["latency_s"] for r in rows], 50), 3)
    return {"summary": summary, "rows": rows}


def _judge(item: EvalItem, result: dict) -> dict:
    sources = []
    if result.get("primary"):
        sources.append(result["primary"]["content"])
    sources += [s["content"] for s in result.get("sources", [])[:3]]
    ctx = "\n\n-----\n\n".join(x[:3000] for x in sources) or "(no sources)"
    user = (f"Question: {item.question}\n\nReference answer: {item.reference_answer or item.gold_quote or '(none)'}\n\n"
            f"System answer: {result.get('answer')}\n\nSOURCES:\n{ctx}")
    try:
        data = json.loads(judge_chat_json(JUDGE_SYSTEM, user))
        return {"faithfulness": float(data.get("faithfulness", 0.0)),
                "correctness": str(data.get("correctness", "incorrect")), "reason": data.get("reason", "")}
    except Exception as exc:
        log.warning("Judge failed for %s: %s", item.id, exc)
        return {"faithfulness": None, "correctness": None, "reason": f"judge error: {exc}"}


def _citation_ok(answer: str, gold_refs: dict) -> bool | None:
    cited = {k: v for k, v in parse_references(answer or "").items() if k in CITATION_KEYS}
    if not cited or not gold_refs:
        return None
    return all(gold_refs.get(k) == v for k, v in cited.items() if k in gold_refs) and any(k in gold_refs for k in cited)


def run_e2e(
    items: list[EvalItem], backend: SearchBackend, scope: str = "all", judge: bool = True, workers: int = 4
) -> dict:
    doc_ids = backend.document_ids_by_filename()

    def one(item: EvalItem) -> dict:
        t0 = time.perf_counter()
        trace = retrieve(item.question, _scope_ids(item, doc_ids, scope), None, backend)
        t1 = time.perf_counter()
        res = generate_answer(trace, backend)
        t2 = time.perf_counter()
        primary = res.get("primary")
        row = {"id": item.id, "type": item.type, "answerable": item.answerable, "question": item.question,
               "document": item.document, "gold_path": item.gold_path, "found": res["found"], "answer": res["answer"],
               "primary_path": primary["path"] if primary else None,
               "primary_document": primary.get("document_filename") if primary else None,
               "quote": primary["quote"] if primary else "", "quote_verified": bool(primary and primary["quote_verified"]),
               "retrieve_s": round(t1 - t0, 3), "generate_s": round(t2 - t1, 3)}
        if item.answerable:
            if primary and primary.get("document_filename") == item.document:
                rel = M.path_relation(primary["path"], item.gold_path)
                row["granularity"] = {"exact": "exact", "ancestor": "broader", "descendant": "narrower"}.get(rel, "wrong")
            else:
                row["granularity"] = "no_answer" if not primary else "wrong"
            row["quote_f1"] = round(M.token_f1(row["quote"], item.gold_quote), 4) if item.gold_quote else None
            row["citation_ok"] = _citation_ok(res["answer"], item.gold_refs)
            if judge:
                row["judge"] = _judge(item, res)
        return row

    rows = _parallel(one, items, workers)
    ans = [r for r in rows if r["answerable"]]
    neg = [r for r in rows if not r["answerable"]]
    gran = defaultdict(int)
    for r in ans:
        gran[r["granularity"]] += 1
    s: dict = {"n": len(rows), "answerable": len(ans), "unanswerable": len(neg), "scope": scope}
    if ans:
        s["section_exact"] = round(gran["exact"] / len(ans), 4)
        s["section_contains"] = round((gran["exact"] + gran["broader"]) / len(ans), 4)
        s["granularity"] = {k: round(v / len(ans), 4) for k, v in sorted(gran.items())}
        found = [r for r in ans if r["found"]]
        s["answer_rate"] = round(len(found) / len(ans), 4)
        s["quote_verified"] = round(M.mean([1.0 if r["quote_verified"] else 0.0 for r in found]), 4) if found else 0.0
        qf = [r["quote_f1"] for r in ans if r.get("quote_f1") is not None]
        s["quote_f1"] = round(M.mean(qf), 4) if qf else None
        cites = [r["citation_ok"] for r in ans if r.get("citation_ok") is not None]
        s["citation_match"] = round(M.mean([1.0 if c else 0.0 for c in cites]), 4) if cites else None
        s["citation_n"] = len(cites)
        judged = [r["judge"] for r in ans if r.get("judge") and r["judge"].get("faithfulness") is not None]
        if judged:
            s["faithfulness"] = round(M.mean([j["faithfulness"] for j in judged]), 4)
            corr = defaultdict(int)
            for j in judged:
                corr[j["correctness"]] += 1
            s["correctness"] = {k: round(v / len(judged), 4) for k, v in sorted(corr.items())}
            s["judge_model"] = effective_judge_model()
    if neg:
        s["abstention"] = round(sum(1 for r in neg if not r["found"]) / len(neg), 4)
    lat = [r["retrieve_s"] + r["generate_s"] for r in rows]
    s["latency_p50_s"] = round(M.percentile(lat, 50), 3)
    s["latency_p95_s"] = round(M.percentile(lat, 95), 3)
    return {"summary": s, "rows": rows}
