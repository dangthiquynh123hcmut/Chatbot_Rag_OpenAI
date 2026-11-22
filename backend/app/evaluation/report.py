from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ..config import get_settings
from ..llm import effective_judge_model
from .dataset import save_jsonl

THRESHOLDS = {
    ("retrieval", "pipeline", "recall@5"): (">=", 0.90),
    ("retrieval", "rerank", "recall@5"): (">=", 0.85),
    ("e2e", None, "section_exact"): (">=", 0.75),
    ("e2e", None, "quote_verified"): (">=", 0.95),
    ("e2e", None, "quote_f1"): (">=", 0.80),
    ("e2e", None, "faithfulness"): (">=", 0.95),
    ("e2e", None, "citation_match"): (">=", 0.95),
    ("e2e", None, "abstention"): (">=", 0.80),
}


def _get(summary: dict, stage: str, branch: str | None, metric: str):
    s = summary.get(stage, {})
    if branch:
        s = s.get("branches", {}).get(branch, {})
    return s.get(metric)


def evaluate_thresholds(summary: dict) -> list[dict]:
    out = []
    for (stage, branch, metric), (op, target) in THRESHOLDS.items():
        value = _get(summary, stage, branch, metric)
        if value is None:
            continue
        ok = value >= target if op == ">=" else value <= target
        out.append({"stage": stage, "branch": branch, "metric": metric, "value": value, "target": target, "pass": ok})
    return out


def _config() -> dict:
    s = get_settings()
    return {"provider": s.llm_provider, "llm_model": s.active_llm_model, "embedding_model": s.active_embedding_model,
            "embedding_dim": s.embedding_dim, "reranker": s.reranker, "chunk_max_chars": s.chunk_max_chars,
            "vector_top_k": s.vector_top_k, "keyword_top_k": s.keyword_top_k,
            "rerank_candidates": s.rerank_candidates, "judge_model": effective_judge_model()}


def _pct(v) -> str:
    return "—" if v is None else f"{v:.1%}" if isinstance(v, float) else str(v)


def to_markdown(summary: dict, checks: list[dict], failures: list[dict]) -> str:
    lines = ["# Báo cáo đánh giá RAG", "", f"- Thời điểm: {summary['created_at']}", f"- Dataset: `{summary['dataset']}`",
             f"- Backend: {summary['backend']}", "- Cấu hình: " + ", ".join(f"{k}={v}" for k, v in summary["config"].items()), ""]
    if checks:
        lines += ["## So với ngưỡng", "", "| Chỉ số | Giá trị | Ngưỡng | Kết quả |", "| --- | --- | --- | --- |"]
        for c in checks:
            name = f"{c['stage']}.{c['branch'] + '.' if c['branch'] else ''}{c['metric']}"
            lines.append(f"| {name} | {_pct(c['value'])} | ≥ {_pct(c['target'])} | {'PASS' if c['pass'] else 'FAIL'} |")
        lines.append("")
    r = summary.get("retrieval")
    if r:
        lines += [f"## Truy hồi ({r['n']} câu, phạm vi: {r['scope']}, reranker: {r['reranker']})", "",
                  "Trúng = mục trả về là mục đáp án hoặc mục cha chứa nó; Exact = đúng mục đáp án.", "",
                  "| Nhánh | n | R@1 | R@3 | R@5 | R@10 | Exact@5 | MRR@10 | nDCG@10 |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
        for name, b in r["branches"].items():
            lines.append(f"| {name} | {b['n']} | {_pct(b['recall@1'])} | {_pct(b['recall@3'])} | {_pct(b['recall@5'])} | "
                         f"{_pct(b['recall@10'])} | {_pct(b['exact@5'])} | {b['mrr@10']:.3f} | {b['ndcg@10']:.3f} |")
        lines += ["", "Recall@5 theo loại câu hỏi:", ""]
        for name, b in r["branches"].items():
            lines.append(f"- {name}: " + ", ".join(f"{t} {_pct(v)}" for t, v in b["recall@5_by_type"].items()))
        lines.append("")
    e = summary.get("e2e")
    if e:
        lines += [f"## End-to-end ({e['n']} câu: {e['answerable']} có đáp án, {e['unanswerable']} không có)", "",
                  "| Chỉ số | Giá trị |", "| --- | --- |"]
        for key in ("section_exact", "section_contains", "answer_rate", "quote_verified", "quote_f1", "citation_match",
                    "faithfulness", "abstention", "latency_p50_s", "latency_p95_s"):
            if key in e and e[key] is not None:
                lines.append(f"| {key} | {e[key] if key.startswith('latency') else _pct(e[key])} |")
        if e.get("granularity"):
            lines.append("| granularity | " + ", ".join(f"{k} {_pct(v)}" for k, v in e["granularity"].items()) + " |")
        if e.get("correctness"):
            lines.append("| correctness | " + ", ".join(f"{k} {_pct(v)}" for k, v in e["correctness"].items()) + " |")
        lines.append("")
    if failures:
        lines += ["## Các ca lỗi tiêu biểu", ""]
        for f in failures[:15]:
            lines.append(f"- **{f['id']}** ({f['type']}) {f['question']}")
            lines.append(f"  - Đáp án: {' > '.join(f.get('gold_path') or []) or '(không có trong tài liệu)'}")
            lines.append(f"  - Hệ thống: {f['got']}")
    return "\n".join(lines) + "\n"


def collect_failures(retrieval: dict | None, e2e: dict | None) -> list[dict]:
    out = []
    if e2e:
        for r in e2e["rows"]:
            if r["answerable"] and r.get("granularity") not in ("exact",):
                out.append({**r, "got": f"[{r.get('granularity')}] " + " > ".join(r.get("primary_path") or [])})
            elif not r["answerable"] and r["found"]:
                out.append({**r, "got": "trả lời thay vì từ chối: " + (r["answer"] or "")[:150]})
    elif retrieval:
        for r in retrieval["rows"]:
            if r["pipeline"]["contains_rank"] is None:
                out.append({**r, "got": "top3: " + " | ".join(r["pipeline"]["top3"])})
    return out


def write_report(out_dir: Path, dataset: str, backend: str, retrieval: dict | None, e2e: dict | None) -> tuple[Path, list[dict]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    summary = {"created_at": stamp, "dataset": dataset, "backend": backend, "config": _config()}
    if retrieval:
        summary["retrieval"] = retrieval["summary"]
        save_jsonl(retrieval["rows"], out_dir / f"{stamp}-retrieval-rows.jsonl")
    if e2e:
        summary["e2e"] = e2e["summary"]
        save_jsonl(e2e["rows"], out_dir / f"{stamp}-e2e-rows.jsonl")
    checks = evaluate_thresholds(summary)
    summary["thresholds"] = checks
    (out_dir / f"{stamp}-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str),
                                                    encoding="utf-8")
    md_path = out_dir / f"{stamp}-report.md"
    md_path.write_text(to_markdown(summary, checks, collect_failures(retrieval, e2e)), encoding="utf-8")
    return md_path, checks
