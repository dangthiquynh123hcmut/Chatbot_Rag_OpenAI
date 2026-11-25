from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import get_settings
from .converters import SUPPORTED_EXTENSIONS, convert_to_markdown
from .hierarchy import build_chunks, parse_markdown, tree_to_markdown


def cmd_preview(args: argparse.Namespace) -> None:
    path = Path(args.file)
    root = parse_markdown(convert_to_markdown(path), path.stem)
    if args.markdown:
        sys.stdout.write(tree_to_markdown(root))
        return
    for n in root.iter():
        if n.kind == "document":
            continue
        print("  " * (n.depth - 1) + f"[{n.kind}] {n.display()}  refs={n.refs}")
    chunks = build_chunks(root, get_settings().chunk_max_chars, get_settings().chunk_overlap_paragraphs)
    print(f"\n{sum(1 for _ in root.iter()) - 1} sections, {len(chunks)} chunks")


def _container():
    """DI container with a migrated database (commands that use PostgreSQL)."""
    from .core.containers import Container
    from .core.database import init_db

    container = Container()
    init_db(container.db())
    return container


def cmd_ingest(args: argparse.Namespace) -> None:
    ingest = _container().ingest_service_provider()
    target = Path(args.path)
    files = [target] if target.is_file() else sorted(p for p in target.rglob("*") if p.suffix.lower() in SUPPORTED_EXTENSIONS)
    for f in files:
        print(f"Ingesting {f} ...")
        doc_id = ingest.ingest_file(f)
        print(f"  -> {doc_id}")


def cmd_ask(args: argparse.Namespace) -> None:
    res = _container().query_service_provider().answer(args.question)
    print(res["answer"])
    if res["primary"]:
        print("\n" + " > ".join(res["primary"]["path"]))
        print(res["primary"]["content"])


def _finish_audit(reports, args: argparse.Namespace) -> None:
    from .audit import STATUS_ORDER, write_reports

    for r in reports:
        print(r.to_text())
        print()
    write_reports(reports, args.json, args.md)
    worst = max((r.status for r in reports), key=lambda s: STATUS_ORDER[s], default="pass")
    counts = {s: sum(1 for r in reports if r.status == s) for s in ("pass", "info", "warn", "fail")}
    print(f"Tổng kết {len(reports)} tài liệu: " + ", ".join(f"{k}={v}" for k, v in counts.items() if v))
    limit = "warn" if args.strict else "fail"
    if STATUS_ORDER[worst] >= STATUS_ORDER[limit]:
        sys.exit(1)


def cmd_audit(args: argparse.Namespace) -> None:
    from .audit import AuditReport, Check, audit_file

    target = Path(args.path)
    files = [target] if target.is_file() else sorted(
        p for p in target.rglob("*") if p.suffix.lower() in SUPPORTED_EXTENSIONS
    )
    reports = []
    for f in files:
        try:
            reports.append(audit_file(f))
        except Exception as exc:
            reports.append(AuditReport(str(f), f.stem, [Check("Chuyển đổi", "fail", str(exc))], {}))
    _finish_audit(reports, args)


def cmd_audit_db(args: argparse.Namespace) -> None:
    import uuid as _uuid

    from .audit_db import audit_database

    session_factory = _container().session_factory_provider()
    reports = audit_database(session_factory, _uuid.UUID(args.doc) if args.doc else None, args.self_retrieval)
    _finish_audit(reports, args)


def cmd_eval_generate(args: argparse.Namespace) -> None:
    from collections import Counter

    from .evaluation.dataset import save_dataset
    from .evaluation.generate import generate_dataset

    items = generate_dataset(Path(args.docs), args.sections, args.per_section, args.unanswerable, args.seed,
                             use_llm=not args.no_llm)
    save_dataset(items, args.out)
    print(f"Đã tạo {len(items)} câu hỏi → {args.out}")
    print("Theo loại: " + ", ".join(f"{k}={v}" for k, v in Counter(i.type for i in items).items()))
    print("Hãy duyệt file JSONL (sửa/xóa câu chưa đạt, đặt reviewed=true) trước khi dùng làm bộ test chính thức.")


def cmd_eval_run(args: argparse.Namespace) -> None:
    from .evaluation.dataset import load_dataset
    from .evaluation.report import write_report
    from .evaluation.runner import run_e2e, run_retrieval

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
        from .evaluation.memory import MemoryBackend

        backend = MemoryBackend.from_paths(Path(args.docs), Path(args.cache_dir))
    else:
        backend = _container().search_backend_provider()
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


def cmd_extract_metadata(args: argparse.Namespace) -> None:
    import uuid as _uuid

    from .metadata import display_label
    from .utils.file_utils import stored_file

    ingest = _container().ingest_service_provider()
    for doc_id in ingest.document_ids(_uuid.UUID(args.doc) if args.doc else None):
        meta = ingest.refresh_metadata(doc_id, stored_file(doc_id), force=args.force)
        if meta is None:
            continue
        print(f"{doc_id}  [{meta.source}] {display_label(meta, meta.title)}  aliases={meta.aliases}")
    print("Chạy 'reindex' cho từng tài liệu nếu muốn cập nhật ngữ cảnh tiêu đề/tác giả trong embedding.")


def cmd_create_user(args: argparse.Namespace) -> None:
    import getpass

    from .core.context import session_context
    from .services.auth_service import UserError

    container = _container()
    password = args.password or getpass.getpass("Mật khẩu: ")
    try:
        with session_context(container.session_factory_provider()):
            user = container.auth_service_provider().create_user(
                args.username, password, args.display_name, "admin" if args.admin else "user"
            )
    except UserError as exc:
        sys.exit(str(exc))
    print(f"Đã tạo tài khoản {user.username} (role={user.role})")


def cmd_check_aws(args: argparse.Namespace) -> None:
    from .aws import caller_identity, rerank_model_arn
    from .llm import chat_json, embed_query
    from types import SimpleNamespace

    from .rerank import _bedrock_rerank

    s = get_settings()
    print(f"Provider: {s.llm_provider} | profile: {s.aws_profile or '(default chain)'} | region: {s.aws_region}")
    steps = []

    def run(name, fn):
        try:
            detail = fn()
            steps.append((name, True, detail))
        except Exception as exc:
            steps.append((name, False, f"{type(exc).__name__}: {exc}"))

    run("STS GetCallerIdentity", lambda: caller_identity()["arn"])
    run(
        f"Embedding {s.bedrock_embedding_model}",
        lambda: f"vector {len(embed_query('Scope of this regulation'))} chiều",
    )
    run(
        f"Converse {s.bedrock_llm_model}",
        lambda: chat_json("You are a connectivity check assistant.", 'Return {"ok": true}'),
    )
    if s.reranker == "bedrock" or args.all:
        dummy = [
            SimpleNamespace(path=["Article 1"], content="Scope of this regulation"),
            SimpleNamespace(path=["Article 2"], content="The weather is nice today"),
        ]
        run(f"Rerank {rerank_model_arn()}", lambda: f"scores {_bedrock_rerank('scope of regulation', dummy)}")
    ok = True
    for name, passed, detail in steps:
        ok &= passed
        print(f"[{'OK' if passed else 'FAIL'}] {name}: {detail}")
    if not ok:
        sys.exit(1)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(required=True)
    p = sub.add_parser("preview", help="Convert + parse hierarchy, no DB/OpenAI needed")
    p.add_argument("file")
    p.add_argument("--markdown", action="store_true")
    p.set_defaults(func=cmd_preview)
    p = sub.add_parser("ingest", help="Ingest a file or folder into PostgreSQL")
    p.add_argument("path")
    p.set_defaults(func=cmd_ingest)
    p = sub.add_parser("ask", help="Ask a question from the terminal")
    p.add_argument("question")
    p.set_defaults(func=cmd_ask)
    for name, func, helptext in (
        ("audit", cmd_audit, "Audit conversion + hierarchy + chunking of a file or folder (no DB/API needed)"),
        ("audit-db", cmd_audit_db, "Audit stored vectors and chunks in PostgreSQL"),
    ):
        p = sub.add_parser(name, help=helptext)
        if name == "audit":
            p.add_argument("path")
        else:
            p.add_argument("--doc", help="Document id (default: all ready documents)")
            p.add_argument("--self-retrieval", type=int, default=0, metavar="N",
                           help="Sample N section titles and check they retrieve their own section")
        p.add_argument("--json", help="Write JSON report to this file")
        p.add_argument("--md", help="Write Markdown report to this file")
        p.add_argument("--strict", action="store_true", help="Exit non-zero on warnings too")
        p.set_defaults(func=func)
    p = sub.add_parser("eval-generate", help="Generate an evaluation dataset (JSONL) from documents")
    p.add_argument("docs", help="File or folder of PDF/DOCX")
    p.add_argument("--out", default="../eval/datasets/generated.jsonl")
    p.add_argument("--sections", type=int, default=20, help="Sections sampled per document")
    p.add_argument("--per-section", type=int, default=2, help="LLM questions per sampled section")
    p.add_argument("--unanswerable", type=int, default=5, help="Unanswerable questions per document")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--no-llm", action="store_true", help="Only template reference questions, no LLM calls")
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
    p = sub.add_parser("extract-metadata", help="(Re)extract document metadata without re-embedding")
    p.add_argument("--doc", help="Document id (default: all ready documents)")
    p.add_argument("--force", action="store_true", help="Also overwrite metadata edited manually")
    p.set_defaults(func=cmd_extract_metadata)
    p = sub.add_parser("create-user", help="Create a login account")
    p.add_argument("username")
    p.add_argument("--password", help="Omit to be prompted")
    p.add_argument("--display-name")
    p.add_argument("--admin", action="store_true")
    p.set_defaults(func=cmd_create_user)
    p = sub.add_parser("check-aws", help="Verify IAM credentials and Bedrock model access")
    p.add_argument("--all", action="store_true", help="Also test Bedrock rerank even if RERANKER is not bedrock")
    p.set_defaults(func=cmd_check_aws)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
