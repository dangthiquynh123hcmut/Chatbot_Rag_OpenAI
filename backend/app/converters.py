from __future__ import annotations

import logging
import re
from pathlib import Path

from .config import get_settings
from .hierarchy import classify_line, nfc

SUPPORTED_EXTENSIONS = {".pdf", ".docx"}
log = logging.getLogger(__name__)


class ConversionError(Exception):
    pass


def _scan_check(path: Path) -> bool:
    import pymupdf

    settings = get_settings()
    with pymupdf.open(path) as doc:
        if doc.page_count == 0:
            raise ConversionError("PDF không có trang nào.")
        step = max(1, doc.page_count // 20)
        sample = list(range(0, doc.page_count, step))[:20]
        empty = sum(1 for i in sample if len(doc[i].get_text().strip()) < 30)
    scanned = empty / len(sample) > 0.6
    if scanned and not settings.enable_ocr:
        raise ConversionError("PDF là bản scan (không có lớp text). Hãy bật ENABLE_OCR.")
    return scanned


def _docling_to_markdown(path: Path, doc_key: str | None, do_ocr: bool = False) -> str:
    from . import docling_pdf

    if not docling_pdf.available():
        raise ConversionError("Chưa cài Docling: chạy `pip install -r requirements-docling.txt`.")
    try:
        md, stats = docling_pdf.convert_document(path, doc_key, do_ocr=do_ocr)
    except Exception as exc:
        log.exception("Docling conversion failed for %s", path.name)
        raise ConversionError(f"Docling không chuyển được {path.name}: {exc}") from exc
    log.info("Docling %s: %s", path.name, stats)
    return light_normalize(md)


def pdf_to_markdown(path: Path, doc_key: str | None = None) -> str:
    return _docling_to_markdown(path, doc_key, do_ocr=_scan_check(path))


def docx_to_markdown(path: Path, doc_key: str | None = None) -> str:
    return _docling_to_markdown(path, doc_key)


TERMINAL = tuple(".:;!?…)\"”»]")
NOISE_PATTERNS = [
    re.compile(r"^\s*-{3,}\s*$"),
    re.compile(r"^\s*(trang|page)?\s*\d{1,4}\s*(/\s*\d{1,4})?\s*$", re.IGNORECASE),
    re.compile(r"^\s*!\[[^\]]*\]\([^)]*\)\s*$"),
    re.compile(r"^\s*<!--.*-->\s*$"),
]


def _is_noise(line: str) -> bool:
    return any(p.match(line) for p in NOISE_PATTERNS)


def _starts_lower(text: str) -> bool:
    t = text.lstrip("*_ \"“(")
    return bool(t) and t[0].isalpha() and t[0].islower()


def normalize_markdown(md: str) -> str:
    md = nfc(md).replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")
    md = re.sub(r"[​﻿]", "", md)
    md = re.sub(r"(\w)-\n(\w)", r"\1\2", md)
    out: list[str] = []
    for raw in md.split("\n"):
        line = raw.rstrip()
        if _is_noise(line):
            continue
        stripped = line.strip()
        if not stripped:
            out.append("")
            continue
        prev = out[-1] if out else ""
        prev_s = prev.strip()
        can_join = (
            prev_s
            and not prev_s.startswith(("#", "|", "-", "*", ">"))
            and not stripped.startswith(("#", "|"))
            and not prev_s.endswith(TERMINAL)
            and _starts_lower(stripped)
            and classify_line(stripped) is None
            and classify_line(prev_s) is None
        )
        if can_join:
            out[-1] = f"{prev_s} {stripped}"
        else:
            out.append(stripped if not stripped.startswith("|") else line)
    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def light_normalize(md: str) -> str:
    md = nfc(md).replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")
    md = re.sub(r"[​﻿]", "", md)
    return re.sub(r"\n{3,}", "\n\n", md).strip() + "\n"


def convert_to_markdown(path: Path, doc_key: str | None = None) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf":
        md = pdf_to_markdown(path, doc_key)
    elif ext == ".docx":
        md = docx_to_markdown(path, doc_key)
    else:
        raise ConversionError(f"Định dạng {ext} chưa được hỗ trợ (chỉ PDF, DOCX).")
    if not md.strip():
        raise ConversionError("Không trích xuất được nội dung từ tài liệu.")
    return md
