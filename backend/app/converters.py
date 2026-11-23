from __future__ import annotations

import re
from pathlib import Path

from .config import get_settings
from .hierarchy import classify_line, nfc

SUPPORTED_EXTENSIONS = {".pdf", ".docx"}


class ConversionError(Exception):
    pass


def pdf_to_markdown(path: Path) -> str:
    import pymupdf
    import pymupdf4llm

    settings = get_settings()
    with pymupdf.open(path) as doc:
        if doc.page_count == 0:
            raise ConversionError("PDF không có trang nào.")
        sample = range(min(doc.page_count, 10))
        text_chars = sum(len(doc[i].get_text().strip()) for i in sample)
        scanned = text_chars < 30 * len(sample)
        if scanned and not settings.enable_ocr:
            raise ConversionError("PDF là bản scan (không có lớp text). Hãy bật ENABLE_OCR.")
        try:
            md = pymupdf4llm.to_markdown(
                doc,
                header=False,
                footer=False,
                use_ocr=settings.enable_ocr,
                force_ocr=scanned,
                ocr_language=settings.ocr_language,
                show_progress=False,
            )
        except Exception as exc:
            raise ConversionError(f"Lỗi chuyển PDF sang Markdown: {exc}") from exc
    if scanned and len(md.strip()) < 50:
        raise ConversionError(
            "PDF là bản scan nhưng OCR không trích được nội dung. "
            "Cài `pip install rapidocr onnxruntime` hoặc Tesseract kèm gói ngôn ngữ 'eng'."
        )
    return md


def docx_to_markdown(path: Path) -> str:
    import mammoth
    from markdownify import markdownify

    style_map = "\n".join(
        [
            "p[style-name='Title'] => h1:fresh",
            "p[style-name='Heading 1'] => h1:fresh",
            "p[style-name='Heading 2'] => h2:fresh",
            "p[style-name='Heading 3'] => h3:fresh",
            "p[style-name='Heading 4'] => h4:fresh",
            "p[style-name='Heading 5'] => h5:fresh",
            "p[style-name='Heading 6'] => h6:fresh",
        ]
    )
    try:
        with open(path, "rb") as f:
            result = mammoth.convert_to_html(
                f,
                style_map=style_map,
                convert_image=mammoth.images.img_element(lambda _: {"src": ""}),
            )
    except Exception as exc:
        raise ConversionError(f"Lỗi đọc DOCX: {exc}") from exc
    return markdownify(result.value, heading_style="ATX", bullets="-", strip=["img", "a"])


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


def convert_to_markdown(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf":
        raw = pdf_to_markdown(path)
    elif ext == ".docx":
        raw = docx_to_markdown(path)
    else:
        raise ConversionError(f"Định dạng {ext} chưa được hỗ trợ (chỉ PDF, DOCX).")
    md = normalize_markdown(raw)
    if not md.strip():
        raise ConversionError("Không trích xuất được nội dung từ tài liệu.")
    return md
