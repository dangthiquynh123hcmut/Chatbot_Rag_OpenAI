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
    "Do not speculate beyond what is visible. "
    "Write plain prose only: no headings, lists or Markdown. Caption: {caption}"
)
# Multi-panel figures: Docling often detects each panel as its own picture, with the caption attached to only
# one of them and the panel titles as separate short text items. These settings decide what counts as one figure.
PANEL_GAP_PT = 24.0
PANEL_LABEL_MAX_WORDS = 8
CROP_PAD_PT = 4.0
SENTENCE_END_RE = re.compile(r"[.!?:;…)\]\"'”’]$")
ENUM_START_RE = re.compile(r"^([a-zđ]|[ivx]{1,4}|\d{1,3})[).]\s", re.IGNORECASE)
MD_HEADING_LINE_RE = re.compile(r"^\s*#{1,6}\s")

_converters: dict[object, object] = {}
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
    panel_titles: list[str] = field(default_factory=list)


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
    merged_panels: int = 0
    joined_paragraphs: int = 0
    notes: list[str] = field(default_factory=list)


Box = tuple[float, float, float, float]  # (left, top, right, bottom), top-left origin, PDF points


@dataclass
class LayoutEntry:
    """Layout facts about one Docling item, enough to decide panel grouping without Docling itself."""

    kind: str  # "picture" | "label" (short text that may be a panel title) | "skip" (caption) | "other"
    page: int | None = None
    box: Box | None = None
    captions: int = 0


def _union(boxes: list[Box]) -> Box:
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def _attached_label(label: Box, pictures: list[Box]) -> bool:
    """A panel title sits just above/below/inside the panels and is centred over one of them."""
    u = _union(pictures)
    if label[3] < u[1] - PANEL_GAP_PT or label[1] > u[3] + PANEL_GAP_PT:
        return False
    centre = (label[0] + label[2]) / 2
    return any(p[0] <= centre <= p[2] and (label[2] - label[0]) <= 1.5 * (p[2] - p[0]) + PANEL_GAP_PT for p in pictures)


def find_panel_groups(entries: list[LayoutEntry]) -> list[tuple[list[int], list[int]]]:
    """Find runs of ≥2 pictures on one page that together carry at most one caption (one figure split into panels).

    Returns (picture indices, panel-title label indices) per group. Short labels may sit right before the first
    picture or between pictures; any other content in between ends the run.
    """
    groups: list[tuple[list[int], list[int]]] = []
    i, n = 0, len(entries)
    while i < n:
        first = entries[i]
        if first.kind != "picture" or first.box is None:
            i += 1
            continue
        pics, between, pending, j = [i], [], [], i + 1
        while j < n:
            e = entries[j]
            if e.kind == "skip":
                j += 1
            elif e.kind == "label" and e.page == first.page and e.box is not None:
                pending.append(j)
                j += 1
            elif e.kind == "picture" and e.page == first.page and e.box is not None:
                pics.append(j)
                between += pending
                pending = []
                j += 1
            else:
                break
        boxes = [entries[p].box for p in pics]
        if len(pics) < 2 or sum(entries[p].captions for p in pics) > 1:
            i += 1
            continue
        if not all(_attached_label(entries[k].box, boxes) for k in between):
            i += 1
            continue
        labels = list(between)
        k = i - 1
        while k >= 0 and entries[k].kind in ("label", "skip"):
            e = entries[k]
            if e.kind == "label":
                if e.page != first.page or e.box is None or not _attached_label(e.box, boxes):
                    break
                labels.insert(0, k)
            k -= 1
        labels += [p for p in pending if _attached_label(entries[p].box, boxes)]
        groups.append((pics, labels))
        i = max(pics) + 1
    return groups


def continues_paragraph(prev: str, nxt: str) -> bool:
    """True when `nxt` is the rest of a sentence that `prev` left unfinished (e.g. cut by a page break or figure)."""
    prev, nxt = prev.rstrip(), nxt.lstrip()
    if not prev or not nxt or prev.startswith(("#", "|", "```", "$$", "![")):
        return False
    return not SENTENCE_END_RE.search(prev) and nxt[0].islower() and not ENUM_START_RE.match(nxt)


def clean_description(text: str) -> str:
    lines = [ln for ln in text.splitlines() if not MD_HEADING_LINE_RE.match(ln)] or [text.lstrip("# ")]
    out = " ".join(lines).replace("**", "").replace("__", "")
    return re.sub(r"\s+", " ", out).strip()


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


def _get_docx_converter():
    # DOCX already carries its structure: Docling reads it directly, no layout/table models involved
    with _converter_lock:
        if "docx" not in _converters:
            from docling.datamodel.base_models import InputFormat
            from docling.document_converter import DocumentConverter

            _converters["docx"] = DocumentConverter(allowed_formats=[InputFormat.DOCX])
        return _converters["docx"]


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


def _item_box(item, page_heights: dict[int, float]) -> tuple[int | None, Box | None]:
    prov = getattr(item, "prov", None) or []
    if not prov:
        return None, None
    p = prov[0]
    height = page_heights.get(p.page_no)
    if height is None:
        return p.page_no, None
    bb = p.bbox.to_top_left_origin(height)
    return p.page_no, (bb.l, bb.t, bb.r, bb.b)


def _crop_page(doc, page_no: int, box: Box):
    """Crop a region from Docling's rendered page image (needs generate_page_images)."""
    page = doc.pages.get(page_no)
    image = getattr(getattr(page, "image", None), "pil_image", None) if page is not None else None
    if image is None:
        return None
    s = image.width / page.size.width
    l, t, r, b = box
    return image.crop((
        max(0, int((l - CROP_PAD_PT) * s)), max(0, int((t - CROP_PAD_PT) * s)),
        min(image.width, int((r + CROP_PAD_PT) * s)), min(image.height, int((b + CROP_PAD_PT) * s)),
    ))


def _compose_panels(panels: list[tuple[object, Box]]):
    """Fallback without a page image: paste each panel's own crop at its position on a white canvas."""
    from PIL import Image

    u = _union([b for _, b in panels])
    s = max(img.width / max(1.0, b[2] - b[0]) for img, b in panels)
    canvas = Image.new("RGB", (max(1, round((u[2] - u[0]) * s)), max(1, round((u[3] - u[1]) * s))), "white")
    for img, (l, t, r, b) in panels:
        size = (max(1, round((r - l) * s)), max(1, round((b - t) * s)))
        canvas.paste(img.convert("RGB").resize(size), (round((l - u[0]) * s), round((t - u[1]) * s)))
    return canvas


def _figure_image(doc, members: list, group_entries: list[LayoutEntry]):
    """Image for one picture, or for a multi-panel group: the page region spanning all panels and their titles."""
    if group_entries:
        page_no = group_entries[0].page
        boxes = [e.box for e in group_entries if e.box is not None]
        if page_no is not None and boxes:
            cropped = _crop_page(doc, page_no, _union(boxes))
            if cropped is not None:
                return cropped
        panels = []
        for m, e in zip(members, group_entries):
            try:
                img = m.get_image(doc)
            except Exception:
                img = None
            if img is not None and e.box is not None:
                panels.append((img, e.box))
        if panels:
            return _compose_panels(panels)
    try:
        return members[0].get_image(doc)
    except Exception:
        return None


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
            prompt = FIGURE_PROMPT.format(caption=f.caption or "(none)")
            if f.panel_titles:
                prompt += "\nThe figure has several panels titled: " + "; ".join(f.panel_titles)
            f.description = clean_description(vision_text(prompt, f.png, max_tokens=300))
        except Exception as exc:
            log.warning("Figure description failed for %s: %s", f.name, exc)

    with ThreadPoolExecutor(max_workers=max(1, min(4, s.bedrock_max_workers))) as pool:
        list(pool.map(describe, chosen))
    stats.described_figures = sum(1 for f in chosen if f.description)


def convert_document(path: Path, doc_key: str | None = None, do_ocr: bool = False) -> tuple[str, ConversionStats]:
    """Convert a PDF or DOCX to Markdown. PDF-only repairs (TOC levels, running-header dedupe, paragraphs cut by
    page breaks, formula crops) are skipped for DOCX, whose heading levels and paragraphs are exact."""
    from docling_core.types.doc import DocItemLabel

    is_pdf = path.suffix.lower() == ".pdf"
    stats = ConversionStats()
    picture_labels = {DocItemLabel.PICTURE, getattr(DocItemLabel, "CHART", DocItemLabel.PICTURE)}
    converter = _get_converter(do_ocr) if is_pdf else _get_docx_converter()
    with _convert_lock:
        result = converter.convert(str(path))
    doc = result.document

    toc = read_toc(path) if is_pdf else []
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
    # Figures, tables and footnotes float: a paragraph may continue after them. Their block indices are kept here
    # so a later text item can be joined back onto the paragraph they interrupted.
    float_blocks: set[int] = set()
    last_para: int | None = None
    text_labels = {DocItemLabel.TEXT, getattr(DocItemLabel, "PARAGRAPH", DocItemLabel.TEXT)}

    items = [item for item, _level in doc.iterate_items()]
    page_heights = {no: page.size.height for no, page in doc.pages.items()}
    entries: list[LayoutEntry] = []
    for item in items:
        page_no, box = _item_box(item, page_heights)
        label = getattr(item, "label", None)
        text = (getattr(item, "text", "") or "").strip()
        if getattr(item, "self_ref", "") in caption_refs:
            kind = "skip"
        elif label in picture_labels:
            kind = "picture"
        elif (label in text_labels or label == DocItemLabel.SECTION_HEADER) and text \
                and len(text.split()) <= PANEL_LABEL_MAX_WORDS and not is_numbered_heading(text) \
                and not SENTENCE_END_RE.search(text):
            kind = "label"
        else:
            kind = "other"
        captions = len(getattr(item, "captions", []) or []) if kind == "picture" else 0
        entries.append(LayoutEntry(kind, page_no, box, captions))
    panel_groups: dict[int, tuple[list[int], list[int]]] = {}
    absorbed: set[int] = set()
    for pics, labels in find_panel_groups(entries):
        panel_groups[pics[0]] = (pics, labels)
        absorbed.update(pics[1:])
        absorbed.update(labels)

    for idx, item in enumerate(items):
        if idx in absorbed:
            continue
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
            if is_pdf and label == DocItemLabel.SECTION_HEADER and not numbered and key in seen_headings:
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
            elif not is_pdf:
                # Word "Heading N" style → N+1 hashes (# is the document title)
                blocks.append("#" * min((getattr(item, "level", 1) or 1) + 1, 6) + " " + text)
            else:
                level = min(last_toc_level + 2, 6) if toc_by_title else 2
                blocks.append("#" * level + " " + text)
        elif label == DocItemLabel.TABLE:
            stats.tables += 1
            caption = item.caption_text(doc).strip()
            md = table_to_markdown(item, doc)
            for part in (caption, md):
                if part:
                    float_blocks.add(len(blocks))
                    blocks.append(part)
        elif label in picture_labels:
            group = panel_groups.get(idx)
            members = [items[i] for i in group[0]] if group else [item]
            stats.figures += len(members)
            caption = next((c for c in (m.caption_text(doc).strip() for m in members) if c), "")
            panel_titles = [(getattr(items[i], "text", "") or "").strip() for i in group[1]] if group else []
            image = None
            if enrich:
                image = _figure_image(doc, members, [entries[i] for i in group[0] + group[1]] if group else [])
            if group:
                stats.merged_panels += len(members) - 1
            if image is not None:
                name = f"figure_{len(figures) + 1}.png"
                figures.append(FigureAsset(name, caption, _png(image), image.width, image.height,
                                           panel_titles=panel_titles))
                figure_slots[name] = len(blocks)
                float_blocks.add(len(blocks))
                blocks.append(name)
            elif caption:
                float_blocks.add(len(blocks))
                blocks.append(caption)
        elif label == DocItemLabel.FORMULA:
            stats.formulas += 1
            # DOCX equations arrive as LaTeX already; only PDF formulas need the vision transcription
            latex = _formula_latex(item, doc, stats) if enrich and is_pdf else re.sub(r"\s+", " ", text)
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
        elif label == DocItemLabel.FOOTNOTE and text:
            float_blocks.add(len(blocks))
            blocks.append(text)
        elif label in text_labels and text:
            if (is_pdf and last_para is not None
                    and all(i in float_blocks for i in range(last_para + 1, len(blocks)))
                    and continues_paragraph(blocks[last_para], text)):
                blocks[last_para] = blocks[last_para].rstrip() + " " + text
                stats.joined_paragraphs += 1
            else:
                last_para = len(blocks)
                blocks.append(text)
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
