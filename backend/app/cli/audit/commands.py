from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ...converters import SUPPORTED_EXTENSIONS
from ..container import build_container


def _finish_audit(reports, args: argparse.Namespace) -> None:
    from .file_audit import STATUS_ORDER, write_reports

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
    from .file_audit import AuditReport, Check, audit_file

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

    from .db_audit import audit_database

    session_factory = build_container().session_factory_provider()
    reports = audit_database(session_factory, _uuid.UUID(args.doc) if args.doc else None, args.self_retrieval)
    _finish_audit(reports, args)


def register(sub) -> None:
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
