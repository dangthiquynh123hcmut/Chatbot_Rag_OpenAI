from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..container import build_container


def cmd_eval_generate(args: argparse.Namespace) -> None:
    from collections import Counter

    from .dataset import save_dataset
    from .generate import generate_dataset

    items = generate_dataset(Path(args.docs), args.sections, args.per_section, args.unanswerable, args.seed,
                             use_llm=not args.no_llm, critique=not args.no_critique, min_score=args.min_score)
    save_dataset(items, args.out)
    print(f"Đã tạo {len(items)} câu hỏi → {args.out}")
    print("Theo loại: " + ", ".join(f"{k}={v}" for k, v in Counter(i.type for i in items).items()))
    print("Hãy duyệt file JSONL (sửa/xóa câu chưa đạt, đặt reviewed=true) trước khi dùng làm bộ test chính thức.")


def cmd_eval_run(args: argparse.Namespace) -> None:
    from .dataset import load_dataset
    from .report import write_report
    from .runner import run_e2e, run_retrieval

    items = load_dataset(args.dataset)
    if args.types:
        wanted = set(args.types.split(","))
        items = [i for i in items if i.type in wanted]
    if args.reviewed_only:
        items = [i for i in items if i.reviewed]
    if args.limit:
        items = items[: args.limit]
    if args.backend == "memory":
        if not args.docs:
            sys.exit("--docs là bắt buộc khi --backend memory")
        from .memory import MemoryBackend

        backend = MemoryBackend.from_paths(Path(args.docs), Path(args.cache_dir))
    else:
        backend = build_container().search_backend_provider()
    retrieval = e2e = None
    if args.stage in ("retrieval", "all"):
        retrieval = run_retrieval(items, backend, args.scope, args.reranker, args.workers)
    if args.stage in ("e2e", "all"):
        e2e = run_e2e(items, backend, args.scope, not args.no_judge, args.workers)
    md_path, checks = write_report(Path(args.out_dir), args.dataset, args.backend, retrieval, e2e)
    print(md_path.read_text(encoding="utf-8"))
    print(f"Báo cáo: {md_path}")
    if args.strict and any(not c["pass"] for c in checks):
        sys.exit(1)


def register(sub) -> None:
    p = sub.add_parser("eval-generate", help="Generate an evaluation dataset (JSONL) from documents")
    p.add_argument("docs", help="File or folder of PDF/DOCX")
    p.add_argument("--out", default="../eval/datasets/generated.jsonl")
    p.add_argument("--sections", type=int, default=20, help="Sections sampled per document")
    p.add_argument("--per-section", type=int, default=2, help="LLM questions per sampled section")
    p.add_argument("--unanswerable", type=int, default=5, help="Unanswerable questions per document")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--no-llm", action="store_true", help="Only template reference questions, no LLM calls")
    p.add_argument("--no-critique", action="store_true",
                   help="Skip the LLM critique filter (groundedness/standalone/relevance)")
    p.add_argument("--min-score", type=int, default=4, choices=range(1, 6),
                   help="Minimum 1-5 critique score required on every criterion")
    p.set_defaults(func=cmd_eval_generate)
    p = sub.add_parser("eval-run", help="Run retrieval and/or end-to-end evaluation")
    p.add_argument("dataset")
    p.add_argument("--stage", choices=["retrieval", "e2e", "all"], default="all")
    p.add_argument("--backend", choices=["memory", "db"], default="db")
    p.add_argument("--docs", help="Documents for --backend memory")
    p.add_argument("--cache-dir", default="../eval/.cache")
    p.add_argument("--scope", choices=["all", "document"], default="all",
                   help="Search across all documents or only the item's document")
    p.add_argument("--reranker", choices=["llm", "bedrock", "cross_encoder", "none"], default=None)
    p.add_argument("--no-judge", action="store_true", help="Skip LLM judge (faithfulness/correctness)")
    p.add_argument("--types", help="Comma-separated question types to include")
    p.add_argument("--reviewed-only", action="store_true")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--out-dir", default="../eval/reports")
    p.add_argument("--strict", action="store_true", help="Exit non-zero if any threshold fails")
    p.set_defaults(func=cmd_eval_run)
