from __future__ import annotations

import json
import re
import statistics
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import get_settings
from .converters import convert_to_markdown
from .hierarchy import INLINE_KINDS, Node, build_chunks, classify_line, nfc, parse_markdown, tree_to_markdown

STATUS_ORDER = {"pass": 0, "info": 1, "warn": 2, "fail": 3}

VIET_VOWELS = "aàáạảãâầấậẩẫăằắặẳẵeèéẹẻẽêềếệểễiìíịỉĩoòóọỏõôồốộổỗơờớợởỡuùúụủũưừứựửữyỳýỵỷỹ"
VIET_LETTERS = set(VIET_VOWELS + "đbcdghklmnpqrstvx" + "fjwz")
VIET_MARKED = set(VIET_VOWELS + "đ") - set("aeiouy")
TONE_MARKS = {"̀", "́", "̃", "̉", "̣"}
SYLLABLE_RE = re.compile(
    rf"^(ngh|ng|nh|ch|gh|gi|kh|ph|qu|th|tr|[bcdđghklmnpqrstvx])?([{VIET_VOWELS}]{{1,3}})(ch|nh|ng|[cmnpt])?$"
)
TOKEN_RE = re.compile(r"[^\W\d_]+")
TABLE_SEP_RE = re.compile(r"^\|\s*:?-{3,}")
STRUCTURAL_KINDS = ("part", "chapter", "section", "subsection", "article")


@dataclass
class Check:
    name: str
    status: str
    summary: str
    details: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


@dataclass
class AuditReport:
    file: str
    title: str
    checks: list[Check]
    stats: dict

    @property
    def status(self) -> str:
        return max((c.status for c in self.checks), key=lambda s: STATUS_ORDER[s], default="pass")

    def to_dict(self) -> dict:
        return {"file": self.file, "title": self.title, "status": self.status, "stats": self.stats,
                "checks": [asdict(c) for c in self.checks]}

    def to_text(self, max_details: int = 5) -> str:
        lines = [f"== {self.file}  [{self.status.upper()}]"]
        lines.append("   " + ", ".join(f"{k}={v}" for k, v in self.stats.items()))
        for c in self.checks:
            lines.append(f"[{c.status.upper():4}] {c.name}: {c.summary}")
            for d in c.details[:max_details]:
                lines.append(f"         - {d}")
            if len(c.details) > max_details:
                lines.append(f"         … và {len(c.details) - max_details} mục khác")
        return "\n".join(lines)

    def to_markdown(self, max_details: int = 10) -> str:
        out = [f"## {self.title}", "", f"- File: `{self.file}`", f"- Kết quả: **{self.status.upper()}**",
               "- " + ", ".join(f"{k}: {v}" for k, v in self.stats.items()), "",
               "| Kiểm tra | Trạng thái | Kết quả |", "| --- | --- | --- |"]
        for c in self.checks:
            out.append(f"| {c.name} | {c.status.upper()} | {c.summary.replace('|', '/')} |")
        for c in self.checks:
            if c.details and c.status != "pass":
                out += ["", f"**{c.name}**", ""]
                out += [f"- {d.replace('|', '/')}" for d in c.details[:max_details]]
        return "\n".join(out) + "\n"


def _status(value: float, warn: float, fail: float, higher_is_better: bool = True) -> str:
    if higher_is_better:
        return "fail" if value < fail else "warn" if value < warn else "pass"
    return "fail" if value > fail else "warn" if value > warn else "pass"


def tokens(text: str) -> list[str]:
    return TOKEN_RE.findall(nfc(text).lower())


def extract_source(path: Path) -> tuple[list[str], int]:
    ext = path.suffix.lower()
    if ext == ".pdf":
        import pymupdf

        pages, tables = [], 0
        with pymupdf.open(path) as doc:
            for page in doc:
                pages.append(page.get_text())
                try:
                    tables += len(page.find_tables().tables)
                except Exception:
                    pass
        return pages, tables
    if ext == ".docx":
        from docx import Document

        d = Document(str(path))
        parts = [p.text for p in d.paragraphs]
        for t in d.tables:
            for row in t.rows:
                parts.extend(cell.text for cell in row.cells)
        return ["\n".join(parts)], len(d.tables)
    raise ValueError(f"Không hỗ trợ {ext}")


def is_valid_syllable(tok: str) -> bool:
    if not SYLLABLE_RE.match(tok):
        return False
    marks = [ch for ch in unicodedata.normalize("NFD", tok) if ch in TONE_MARKS]
    return len(marks) <= 1


def check_unicode(raw_pages: list[str], markdown: str) -> Check:
    raw = "".join(raw_pages)
    raw_toks = TOKEN_RE.findall(raw)
    non_nfc = sum(1 for t in raw_toks if unicodedata.normalize("NFC", t) != t)
    md_ok = unicodedata.is_normalized("NFC", markdown)
    ratio = non_nfc / max(1, len(raw_toks))
    status = "fail" if not md_ok else ("info" if non_nfc else "pass")
    summary = f"Markdown {'đã' if md_ok else 'CHƯA'} chuẩn NFC; nguồn có {non_nfc} từ không ở dạng NFC ({ratio:.1%})"
    return Check("Unicode NFC", status, summary, metrics={"source_non_nfc_ratio": round(ratio, 4), "markdown_nfc": md_ok})


def check_vietnamese(markdown: str) -> Check:
    toks = tokens(markdown)
    odd_char = [t for t in toks if any(ch not in VIET_LETTERS and not ("a" <= ch <= "z") for ch in t)]
    viet = [t for t in toks if any(ch in VIET_MARKED for ch in t)]
    if len(viet) < 50:
        return Check("Chất lượng chữ tiếng Việt", "info", f"Ít chữ tiếng Việt có dấu ({len(viet)} từ) – bỏ qua",
                     metrics={"vietnamese_tokens": len(viet)})
    invalid = [t for t in viet if not is_valid_syllable(t)]
    inv_ratio = len(invalid) / len(viet)
    odd_ratio = len(odd_char) / max(1, len(toks))
    status = max(_status(inv_ratio, 0.03, 0.15, False), _status(odd_ratio, 0.01, 0.05, False), key=STATUS_ORDER.get)
    top = [f"{w} ×{n}" for w, n in Counter(invalid + odd_char).most_common(15)]
    summary = f"{inv_ratio:.1%} âm tiết không hợp lệ, {odd_ratio:.1%} từ có ký tự lạ (nghi lỗi font TCVN3/VNI hoặc OCR)"
    return Check("Chất lượng chữ tiếng Việt", status, summary, top,
                 {"invalid_syllable_ratio": round(inv_ratio, 4), "odd_char_ratio": round(odd_ratio, 4)})


def check_coverage(raw_pages: list[str], markdown: str) -> Check:
    md_counts = Counter(tokens(markdown))
    src_counts = Counter(t for p in raw_pages for t in tokens(p))
    total = sum(src_counts.values())
    if total == 0:
        return Check("Độ đầy đủ nội dung", "warn", "File gốc không có lớp chữ (bản scan) – không đo được, cần OCR")
    kept = sum(min(n, md_counts[t]) for t, n in src_counts.items())
    recall = kept / total
    details = []
    if len(raw_pages) > 1:
        for i, p in enumerate(raw_pages, 1):
            pc = Counter(tokens(p))
            n = sum(pc.values())
            if n >= 20:
                r = sum(min(c, md_counts[t]) for t, c in pc.items()) / n
                if r < 0.6:
                    details.append(f"Trang {i}: chỉ giữ {r:.0%} nội dung")
    missing = Counter({t: n - md_counts[t] for t, n in src_counts.items() if n > md_counts[t]})
    if missing:
        details.append("Từ bị mất nhiều nhất: " + ", ".join(f"{t} ×{n}" for t, n in missing.most_common(12)))
    status = _status(recall, 0.97, 0.90)
    if details and status == "pass" and any(d.startswith("Trang") for d in details):
        status = "warn"
    return Check("Độ đầy đủ nội dung", status, f"Giữ được {recall:.1%} từ của file gốc ({kept}/{total})",
                 details, {"token_recall": round(recall, 4)})


def check_tables(source_tables: int, markdown: str) -> Check:
    md_tables = sum(1 for ln in markdown.splitlines() if TABLE_SEP_RE.match(ln.strip()))
    status = "pass" if md_tables >= source_tables else "warn"
    return Check("Bảng biểu", status, f"File gốc {source_tables} bảng, Markdown {md_tables} bảng",
                 metrics={"source_tables": source_tables, "markdown_tables": md_tables})


def _numbers_gaps(nums: list[int]) -> tuple[list[int], list[int]]:
    if not nums:
        return [], []
    dups = sorted(n for n, c in Counter(nums).items() if c > 1)
    gaps = sorted(set(range(min(nums), max(nums) + 1)) - set(nums))
    return gaps, dups


def _int_part(num: str) -> int | None:
    m = re.match(r"\d+", num or "")
    return int(m.group()) if m else None


def check_sequences(root: Node) -> Check:
    details: list[str] = []
    articles = [n for n in root.iter() if n.kind == "article"]
    nums = [v for v in (_int_part(a.number) for a in articles) if v is not None]
    gaps, dups = _numbers_gaps(nums)
    if gaps:
        details.append(f"Thiếu Điều: {', '.join(map(str, gaps[:30]))}")
    if dups:
        details.append(f"Điều bị trùng: {', '.join(map(str, dups[:30]))}")
    for parent in root.iter():
        for kind, label in (("chapter", "Chương"), ("section", "Mục"), ("subsection", "Tiểu mục")):
            kids = [_int_part(c.number) for c in parent.children if c.kind == kind]
            kids = [k for k in kids if k is not None]
            g, d = _numbers_gaps(kids)
            if kids and min(kids) != 1 and kind != "chapter":
                g = list(range(1, min(kids))) + g
            if g:
                details.append(f"{parent.display()}: thiếu {label} {', '.join(map(str, g[:20]))}")
            if d:
                details.append(f"{parent.display()}: trùng {label} {', '.join(map(str, d[:20]))}")
    status = "warn" if details else "pass"
    summary = f"{len(articles)} Điều" + (f", phát hiện {len(details)} vấn đề về số thứ tự" if details else ", số thứ tự liên tục")
    return Check("Tính liên tục số thứ tự", status, summary, details, {"articles": len(articles)})


def check_missed_headings(root: Node) -> Check:
    details = []
    for n in root.iter():
        for ln in n.lines:
            cand = classify_line(ln)
            if cand and cand.kind in STRUCTURAL_KINDS:
                where = n.display() if n.kind != "document" else "Phần mở đầu"
                details.append(f"Trong '{where}': {ln[:110]}")
    status = "warn" if details else "pass"
    return Check("Tiêu đề bị bỏ sót", status,
                 f"{len(details)} dòng trông giống tiêu đề nhưng nằm trong thân bài" if details else "Không có",
                 details, {"suspects": len(details)})


def check_section_sizes(root: Node, max_chars: int) -> Check:
    long_leaves, empty = [], []
    for n in root.iter():
        if n.kind == "document":
            continue
        if not n.children and len(n.full_markdown()) > 3 * max_chars:
            long_leaves.append(f"{' > '.join(n.path)} ({len(n.full_markdown())} ký tự)")
        if n.kind not in INLINE_KINDS and not n.own_text and not n.children:
            empty.append(" > ".join(n.path))
    details = [f"Mục lá rất dài (nghi sót tiêu đề con): {x}" for x in long_leaves]
    details += [f"Tiêu đề rỗng (nghi nhận nhầm hoặc mất nội dung): {x}" for x in empty]
    status = "warn" if details else "pass"
    summary = f"{len(long_leaves)} mục lá > {3 * max_chars} ký tự, {len(empty)} tiêu đề không có nội dung"
    return Check("Kích thước mục", status, summary, details,
                 {"long_leaves": len(long_leaves), "empty_headings": len(empty)})


def check_orphan_text(root: Node) -> Check:
    total = sum(len(n.own_text) for n in root.iter())
    pre = len(root.own_text)
    ratio = pre / max(1, total)
    if not root.children:
        return Check("Văn bản mồ côi", "fail", "Không nhận diện được cấu trúc nào – toàn bộ nằm ở phần mở đầu",
                     metrics={"preamble_ratio": 1.0})
    status = _status(ratio, 0.15, 0.5, False)
    return Check("Văn bản mồ côi", status, f"Phần mở đầu chiếm {ratio:.1%} nội dung",
                 metrics={"preamble_ratio": round(ratio, 4)})


def check_repeated_lines(markdown: str, pages: int) -> Check:
    lines = [ln.strip() for ln in markdown.splitlines()]
    cand = Counter(ln for ln in lines if 5 <= len(ln) <= 80 and not ln.startswith(("|", "#")))
    threshold = max(3, int(pages * 0.3))
    rep = [(ln, c) for ln, c in cand.most_common(10) if c >= threshold]
    status = "warn" if rep else "pass"
    return Check("Header/footer còn sót", status,
                 f"{len(rep)} dòng lặp ≥ {threshold} lần" if rep else "Không có dòng lặp bất thường",
                 [f"'{ln}' ×{c}" for ln, c in rep])


def _squash(s: str) -> str:
    return re.sub(r"[\s#]+", "", nfc(s))


def chunk_within(content: str, scope: str) -> bool:
    sc = _squash(scope)
    if _squash(content) in sc:
        return True
    head, sep, body = content.partition("\n\n")
    return bool(sep) and _squash(head) in sc and _squash(body) in sc


def check_chunks(root: Node, max_chars: int, overlap: int) -> Check:
    chunks = build_chunks(root, max_chars, overlap)
    if not chunks:
        return Check("Chunk", "fail", "Không tạo được chunk nào")
    lengths = [len(c.content) for c in chunks]
    violations, oversize = [], []
    for c in chunks:
        scope = c.node.full_markdown() if c.node.kind != "document" else c.node.own_text
        if not chunk_within(c.content, scope):
            violations.append(" > ".join(c.node.path) if c.node.kind != "document" else "Phần mở đầu")
        if len(c.content) > max_chars + 50:
            oversize.append(f"{' > '.join(c.node.path)} ({len(c.content)} ký tự)")
    split = sum(1 for c in chunks if c.part > 0)
    details = [f"Chunk vượt ra ngoài mục của nó: {v}" for v in violations]
    details += [f"Chunk quá dài: {o}" for o in oversize]
    status = "fail" if violations else ("warn" if oversize else "pass")
    summary = (f"{len(chunks)} chunk, dài TB {statistics.mean(lengths):.0f}, max {max(lengths)} ký tự, "
               f"{split} phần tách từ mục dài")
    return Check("Chunk", status, summary, details,
                 {"chunks": len(chunks), "mean_len": round(statistics.mean(lengths)), "max_len": max(lengths),
                  "split_parts": split, "violations": len(violations)})


def audit_file(path: Path) -> AuditReport:
    settings = get_settings()
    raw_pages, source_tables = extract_source(path)
    md = convert_to_markdown(path)
    root = parse_markdown(md, path.stem)
    canonical = tree_to_markdown(root)
    kinds = Counter(n.kind for n in root.iter() if n.kind != "document")
    stats = {"pages": len(raw_pages) if path.suffix.lower() == ".pdf" else "-", "sections": sum(kinds.values()),
             **{k: v for k, v in kinds.most_common()}}
    checks = [
        check_unicode(raw_pages, canonical),
        check_vietnamese(canonical),
        check_coverage(raw_pages, canonical),
        check_tables(source_tables, canonical),
        check_sequences(root),
        check_missed_headings(root),
        check_section_sizes(root, settings.chunk_max_chars),
        check_orphan_text(root),
        check_repeated_lines(canonical, len(raw_pages)),
        check_chunks(root, settings.chunk_max_chars, settings.chunk_overlap_paragraphs),
    ]
    return AuditReport(str(path), path.stem, checks, stats)


def write_reports(reports: list[AuditReport], json_path: str | None, md_path: str | None) -> None:
    if json_path:
        Path(json_path).write_text(json.dumps([r.to_dict() for r in reports], ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    if md_path:
        body = "# Báo cáo audit tài liệu\n\n" + "\n".join(r.to_markdown() for r in reports)
        Path(md_path).write_text(body, encoding="utf-8")
