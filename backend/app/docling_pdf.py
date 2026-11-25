from __future__ import annotations

import io
import logging
import re
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from .config import get_settings

log = logging.getLogger(__name__)

FIGURE_NAME_RE = re.compile(r"^figure_\d{1,4}\.png$")
JUNK_HEADING_RE = re.compile(
    r"^(click here to view code image|view code image)$"
    r"|^\S+\.(h|hpp|c|cc|cpp|cs|java|kt|py|js|ts|go|rb|swift|sql|xml|json|ya?ml)$",
    re.IGNORECASE,
)
CODE_LIKE_RE = re.compile(r"[{};]|==|=>|::|\w\(\)")
NUMBERED_HEADING_RE = re.compile(r"^\d{1,2}(\.\d{1,2}){0,4}\.?\s+[A-Z][^.!?]{1,90}$")
SCI_EXPONENT_RE = re.compile(r"([·×]\s*)10(-?\d{1,3})\b")
TOC_PREFIX_RE = re.compile(r"^(chapter\s+\d+|part\s+[ivxlc\d]+|appendix\s+[a-z\d]+|[ivxlc]+)\s+", re.IGNORECASE)

FORMULA_PROMPT = (
    "Transcribe the mathematical formula in this image into LaTeX. "
    "Return only the LaTeX source, without $ delimiters, code fences or explanation."
)
FIGURE_PROMPT = (
    "This image is a figure from a document. Describe it for search indexing in 2-4 sentences: "
    "what it shows, its main components and labels, and how they relate. "
    "Do not speculate beyond what is visible. Caption: {caption}"
)

_converters: dict[bool, object] = {}
_converter_lock = threading.Lock()
_convert_lock = threading.Lock()


@dataclass
class FigureAsset:
    name: str
    caption: str
    png: bytes
    width: int
    height: int
    description: str = ""


@dataclass
class ConversionStats:
    headings: int = 0
    toc_matched: int = 0
    junk_headings: int = 0
    tables: int = 0
    figures: int = 0
    formulas: int = 0
    formulas_vlm: int = 0
    described_figures: int = 0
    notes: list[str] = field(default_factory=list)


def available() -> bool:
    try:
        import docling.document_converter
    except Exception:
        return False
    return True


def _get_converter(do_ocr: bool):
    with _converter_lock:
        if do_ocr in _converters:
            return _converters[do_ocr]
        from docling.datamodel.accelerator_options import AcceleratorOptions
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
        from docling.document_converter import DocumentConverter, PdfFormatOption

        s = get_settings()
        opts = PdfPipelineOptions(artifacts_path=s.docling_artifacts_path or None)
        opts.do_ocr = do_ocr
        opts.do_table_structure = True
        opts.table_structure_options.mode = (
            TableFormerMode.ACCURATE if s.docling_table_mode.lower() == "accurate" else TableFormerMode.FAST
        )
        opts.table_structure_options.do_cell_matching = True
        opts.do_formula_enrichment = False
        opts.generate_picture_images = True
        opts.generate_page_images = s.formula_vlm
        opts.images_scale = 2.0
        opts.accelerator_options = AcceleratorOptions(num_threads=s.docling_threads)
        converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})
        _converters[do_ocr] = converter
        return converter


def norm_title(text: str) -> str:
    t = re.sub(r"\s+", " ", text.strip().lower())
    t = TOC_PREFIX_RE.sub("", t)
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def read_toc(path: Path) -> list[tuple[int, str, int]]:
    try:
        import pymupdf

        with pymupdf.open(path) as doc:
            return [(lvl, title.strip(), page) for lvl, title, page in doc.get_toc() if title.strip()]
    except Exception:
        return []


def _cell(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("|", "\\|")).strip()


def table_to_markdown(table, doc) -> str:
    grid = table.data.grid
    if not grid or not grid[0]:
        return ""
    n_cols = max(len(r) for r in grid)
    rows = [[(r[i] if i < len(r) else None) for i in range(n_cols)] for r in grid]
    n_header = 0
    for row in rows:
        if any(c is not None and c.column_header for c in row) and n_header < len(rows) - 1:
            n_header += 1
        else:
            break
    if n_header == 0:
        n_header = 1
    headers = []
    for col in range(n_cols):
        parts: list[str] = []
        for r in range(n_header):
            text = _cell(rows[r][col].text if rows[r][col] is not None else "")
            if text and (not parts or parts[-1] != text):
                parts.append(text)
        headers.append(" / ".join(parts) or f"Column {col + 1}")
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * n_cols) + "|"]
    for row in rows[n_header:]:
        lines.append("| " + " | ".join(_cell(c.text if c is not None else "") for c in row) + " |")
    return SCI_EXPONENT_RE.sub(r"\g<1>10^\2", "\n".join(lines))


def is_numbered_heading(text: str) -> bool:
    return bool(NUMBERED_HEADING_RE.match(text)) and len(text.split()) <= 12


def _png(image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _clean_latex(text: str) -> str:
    t = text.strip()
    t = re.sub(r"^```(?:latex|tex)?\s*|\s*```$", "", t).strip()
    t = t.strip("$").strip()
    return re.sub(r"\s+", " ", t)


def _formula_latex(item, doc, stats: ConversionStats) -> str:
    fallback = re.sub(r"\s+", " ", (getattr(item, "text", "") or getattr(item, "orig", "") or "")).strip()
    if not get_settings().formula_vlm:
        return fallback
    try:
        image = item.get_image(doc)
    except Exception:
        image = None
    if image is None:
        return fallback
    try:
        from .llm import vision_text

        latex = _clean_latex(vision_text(FORMULA_PROMPT, _png(image), max_tokens=400))
    except Exception as exc:
        log.warning("Formula transcription failed: %s", exc)
        return fallback
    if latex:
        stats.formulas_vlm += 1
        return latex
    return fallback


def _describe_figures(figures: list[FigureAsset], stats: ConversionStats) -> None:
    s = get_settings()
    if not s.figure_descriptions or not figures:
        return
    eligible = [f for f in figures if min(f.width, f.height) >= s.min_figure_pixels]
    eligible.sort(key=lambda f: (not f.caption, -(f.width * f.height)))
    chosen = eligible[: s.max_figure_descriptions]
    if len(eligible) > len(chosen):
        stats.notes.append(f"Chỉ mô tả {len(chosen)}/{len(eligible)} hình (MAX_FIGURE_DESCRIPTIONS)")
    from .llm import vision_text

    def describe(f: FigureAsset) -> None:
        try:
            f.description = re.sub(
                r"\s+", " ", vision_text(FIGURE_PROMPT.format(caption=f.caption or "(none)"), f.png, max_tokens=300)
            ).strip()
        except Exception as exc:
            log.warning("Figure description failed for %s: %s", f.name, exc)

    with ThreadPoolExecutor(max_workers=max(1, min(4, s.bedrock_max_workers))) as pool:
        list(pool.map(describe, chosen))
    stats.described_figures = sum(1 for f in chosen if f.description)


def convert_pdf(path: Path, doc_key: str | None = None, do_ocr: bool = False) -> tuple[str, ConversionStats]:
    from docling_core.types.doc import DocItemLabel

    stats = ConversionStats()
    picture_labels = {DocItemLabel.PICTURE, getattr(DocItemLabel, "CHART", DocItemLabel.PICTURE)}
    with _convert_lock:
        result = _get_converter(do_ocr).convert(str(path))
    doc = result.document

    toc = read_toc(path)
    toc_by_title: dict[str, tuple[int, str]] = {}
    for level, title, _ in toc:
        toc_by_title.setdefault(norm_title(title), (level, title))
    last_toc_level = 1

    caption_refs: set[str] = set()
    for item in list(doc.tables) + list(doc.pictures):
        caption_refs.update(c.cref for c in getattr(item, "captions", []) or [])

    enrich = doc_key is not None
    blocks: list[str] = []
    figures: list[FigureAsset] = []
    figure_slots: dict[str, int] = {}
    seen_headings: set[str] = set()
    title_seen = False

    for item, _level in doc.iterate_items():
        label = getattr(item, "label", None)
        ref = getattr(item, "self_ref", "")
        if ref in caption_refs:
            continue
        text = (getattr(item, "text", "") or "").strip()

        if label == DocItemLabel.TEXT and is_numbered_heading(text):
            label = DocItemLabel.SECTION_HEADER
        if label == DocItemLabel.TITLE:
            title_seen = True
        elif label == DocItemLabel.SECTION_HEADER and not title_seen:
            title_seen = True
            prov = getattr(item, "prov", None) or []
            if prov and prov[0].page_no == 1 and not is_numbered_heading(text) and norm_title(text) != "abstract":
                label = DocItemLabel.TITLE
        if label in (DocItemLabel.TITLE, DocItemLabel.SECTION_HEADER) and text:
            key = re.sub(r"^[\d ]+", "", norm_title(text))
            numbered = is_numbered_heading(text)
            if label == DocItemLabel.SECTION_HEADER and not numbered and key in seen_headings:
                stats.junk_headings += 1
                continue
            if label == DocItemLabel.TITLE or numbered:
                seen_headings.add(key)
        if label == DocItemLabel.TITLE:
            blocks.append(f"# {text}")
        elif label == DocItemLabel.SECTION_HEADER:
            if JUNK_HEADING_RE.match(text):
                stats.junk_headings += 1
                if not text.lower().startswith(("click here", "view code")):
                    blocks.append(text)
                continue
            if len(text) > 150 or CODE_LIKE_RE.search(text):
                stats.junk_headings += 1
                blocks.append(f"```\n{text}\n```" if CODE_LIKE_RE.search(text) else text)
                continue
            stats.headings += 1
            match = toc_by_title.get(norm_title(text))
            if match:
                stats.toc_matched += 1
                last_toc_level, title = match
                blocks.append("#" * min(last_toc_level + 1, 6) + " " + title)
            else:
                level = min(last_toc_level + 2, 6) if toc_by_title else 2
                blocks.append("#" * level + " " + text)
        elif label == DocItemLabel.TABLE:
            stats.tables += 1
            caption = item.caption_text(doc).strip()
            md = table_to_markdown(item, doc)
            if caption:
                blocks.append(caption)
            if md:
                blocks.append(md)
        elif label in picture_labels:
            stats.figures += 1
            caption = item.caption_text(doc).strip()
            image = None
            if enrich:
                try:
                    image = item.get_image(doc)
                except Exception:
                    image = None
            if image is not None:
                name = f"figure_{len(figures) + 1}.png"
                figures.append(FigureAsset(name, caption, _png(image), image.width, image.height))
                figure_slots[name] = len(blocks)
                blocks.append(name)
            elif caption:
                blocks.append(caption)
        elif label == DocItemLabel.FORMULA:
            stats.formulas += 1
            latex = _formula_latex(item, doc, stats) if enrich else re.sub(r"\s+", " ", text)
            if latex:
                blocks.append(f"$$ {latex} $$")
        elif label == DocItemLabel.CODE:
            lang = getattr(getattr(item, "code_language", None), "value", "") or ""
            lang = "" if lang.lower() == "unknown" else lang.lower()
            blocks.append(f"```{lang}\n{item.text}\n```")
        elif label == DocItemLabel.LIST_ITEM:
            marker = (getattr(item, "marker", "") or "").strip()
            enumerated = getattr(item, "enumerated", False)
            prefix = marker if enumerated and marker else "-"
            blocks.append(f"{prefix} {text}")
        elif label in (DocItemLabel.PAGE_HEADER, DocItemLabel.PAGE_FOOTER):
            continue
        elif text:
            blocks.append(text)

    if enrich and figures:
        _describe_figures(figures, stats)
        target = get_settings().figure_dir / doc_key
        shutil.rmtree(target, ignore_errors=True)
        target.mkdir(parents=True, exist_ok=True)
        for f in figures:
            (target / f.name).write_bytes(f.png)
            parts = [f"![{(f.caption or 'Figure').replace(']', ')')}](figure://{doc_key}/{f.name})"]
            if f.caption:
                parts.append(f.caption)
            if f.description:
                parts.append(f"Figure description: {f.description}")
            blocks[figure_slots[f.name]] = "\n\n".join(parts)

    if toc_by_title and stats.headings:
        stats.notes.append(f"{stats.toc_matched}/{stats.headings} tiêu đề khớp mục lục nhúng ({len(toc)} mục)")
    markdown = "\n\n".join(b for b in blocks if b.strip())
    return markdown, stats


def figure_path(doc_key: str, name: str) -> Path | None:
    if not FIGURE_NAME_RE.match(name):
        return None
    try:
        uuid_str = str(uuid.UUID(doc_key))
    except ValueError:
        return None
    path = get_settings().figure_dir / uuid_str / name
    return path if path.is_file() else None


def remove_figures(doc_key: str) -> None:
    shutil.rmtree(get_settings().figure_dir / doc_key, ignore_errors=True)
