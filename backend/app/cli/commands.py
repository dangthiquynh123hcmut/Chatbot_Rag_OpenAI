from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..config import get_settings
from ..converters import SUPPORTED_EXTENSIONS, convert_to_markdown
from ..hierarchy import build_chunks, parse_markdown, tree_to_markdown
from .container import build_container


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


def cmd_ingest(args: argparse.Namespace) -> None:
    ingest = build_container().ingest_service_provider()
    target = Path(args.path)
    files = [target] if target.is_file() else sorted(p for p in target.rglob("*") if p.suffix.lower() in SUPPORTED_EXTENSIONS)
    for f in files:
        print(f"Ingesting {f} ...")
        doc_id = ingest.ingest_file(f)
        print(f"  -> {doc_id}")


def cmd_ask(args: argparse.Namespace) -> None:
    res = build_container().query_service_provider().answer(args.question)
    print(res["answer"])
    if res["primary"]:
        print("\n" + " > ".join(res["primary"]["path"]))
        print(res["primary"]["content"])


def cmd_extract_metadata(args: argparse.Namespace) -> None:
    import uuid as _uuid

    from ..metadata import display_label
    from ..utils.file_utils import stored_file

    ingest = build_container().ingest_service_provider()
    for doc_id in ingest.document_ids(_uuid.UUID(args.doc) if args.doc else None):
        meta = ingest.refresh_metadata(doc_id, stored_file(doc_id), force=args.force)
        if meta is None:
            continue
        print(f"{doc_id}  [{meta.source}] {display_label(meta, meta.title)}  aliases={meta.aliases}")
    print("Chạy 'reindex' cho từng tài liệu nếu muốn cập nhật ngữ cảnh tiêu đề/tác giả trong embedding.")


def cmd_create_user(args: argparse.Namespace) -> None:
    import getpass

    from ..core.context import session_context
    from ..services.auth_service import UserError

    container = build_container()
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
    from types import SimpleNamespace

    from ..aws import caller_identity, rerank_model_arn
    from ..llm import chat_json, embed_query
    from ..rerank import _bedrock_rerank

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


def register_pipeline(sub) -> None:
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


def register_admin(sub) -> None:
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
