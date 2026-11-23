from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

DOC_TYPES = ("paper", "book", "report", "manual", "other")
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>]+)", re.IGNORECASE)
ARXIV_RE = re.compile(r"\barxiv[:\s]*(\d{4}\.\d{4,5})(?:v\d+)?", re.IGNORECASE)
ISBN_RE = re.compile(r"\bISBN(?:-1[03])?[:\s]*((?:97[89][\s-]?)?(?:\d[\s-]?){9}[\dX])", re.IGNORECASE)
JUNK_PDF_TITLE = re.compile(r"^(microsoft word|untitled|document\d*$|.*\.(docx?|pdf|tex|indd)$)", re.IGNORECASE)
GENERIC_ALIASES = {
    "the book", "this book", "the paper", "this paper", "introduction", "abstract", "preface", "contents",
    "chapter", "appendix", "references", "the author", "the authors",
}

METADATA_SYSTEM = """You extract bibliographic metadata from the first pages of a document (paper, book, report or manual).
Use ONLY information visible in the text. Leave a field empty if it is not stated.
Fields:
- "title": full title without subtitle noise; "short_title": common short name if any (e.g. main title before a colon).
- "authors": list of author or editor full names, in order.
- "year": publication year as an integer, or null.
- "doc_type": one of paper, book, report, manual, other.
- "publisher": journal, conference or publisher.
- "doi", "arxiv_id", "isbn": identifiers if printed.
- "aliases": 0-5 names a reader would plausibly use to refer to this document in a question
  (short title, well-known nickname such as "the Transformer paper", acronym of the title). No generic words.
- "summary": one sentence describing what the document is about.
Return JSON with exactly these keys."""


@dataclass
class DocMeta:
    title: str = ""
    short_title: str = ""
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    doc_type: str = "other"
    publisher: str = ""
    doi: str = ""
    arxiv_id: str = ""
    isbn: str = ""
    aliases: list[str] = field(default_factory=list)
    summary: str = ""
    source: str = "auto"

    @classmethod
    def from_dict(cls, data: dict | None) -> "DocMeta":
        data = data or {}
        meta = cls(**{k: data[k] for k in cls.__dataclass_fields__ if k in data and data[k] is not None})
        return meta.normalized()

    def to_dict(self) -> dict:
        return asdict(self)

    def normalized(self) -> "DocMeta":
        self.title = _clean(self.title)
        self.short_title = _clean(self.short_title)
        self.authors = [a for a in (_clean(x) for x in self.authors or []) if a][:20]
        self.year = _year(self.year)
        self.doc_type = self.doc_type if self.doc_type in DOC_TYPES else "other"
        self.publisher = _clean(self.publisher)
        self.doi = _clean(self.doi).rstrip(".,;)")
        self.arxiv_id = _clean(self.arxiv_id)
        self.isbn = re.sub(r"[^\dX]", "", (self.isbn or "").upper())
        if len(self.isbn) not in (10, 13):
            self.isbn = ""
        self.summary = _clean(self.summary)[:400]
        self.aliases = clean_aliases(self.aliases, self.title, self.short_title)
        self.source = self.source if self.source in ("auto", "manual") else "auto"
        return self


def _clean(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _year(value) -> int | None:
    try:
        y = int(str(value).strip()[:4])
    except (TypeError, ValueError):
        return None
    return y if 1400 <= y <= 2100 else None


def _is_acronym(word: str) -> bool:
    return len(word) >= 3 and sum(1 for c in word if c.isupper()) >= 2


def _distinctive_single_word(word: str, title: str) -> bool:
    return _is_acronym(word) or word.lower() == title.strip().lower()


def clean_aliases(aliases: list[str] | None, title: str = "", short_title: str = "") -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    candidates = list(aliases or [])
    if short_title:
        candidates.insert(0, short_title)
    if ":" in title:
        candidates.insert(0, title.split(":", 1)[0])
    for alias in candidates:
        a = _clean(alias).strip("\"'“”")
        key = a.lower()
        if not a or key in seen or key in GENERIC_ALIASES or key == title.lower():
            continue
        words = a.split()
        if len(words) == 1 and not _distinctive_single_word(a, title):
            continue
        if len(a) < 3:
            continue
        seen.add(key)
        out.append(a)
    return out[:8]


def find_identifiers(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    if m := DOI_RE.search(text):
        out["doi"] = m.group(1).rstrip(".,;)")
    if m := ARXIV_RE.search(text):
        out["arxiv_id"] = m.group(1)
    if m := ISBN_RE.search(text):
        digits = re.sub(r"[^\dX]", "", m.group(1).upper())
        if len(digits) in (10, 13):
            out["isbn"] = digits
    return out


def pdf_info(path: Path) -> dict[str, str]:
    if path.suffix.lower() != ".pdf":
        return {}
    try:
        import pymupdf

        with pymupdf.open(path) as doc:
            info = doc.metadata or {}
    except Exception:
        return {}
    out = {}
    title = _clean(info.get("title"))
    if title and len(title) > 3 and not JUNK_PDF_TITLE.match(title):
        out["title"] = title
    author = _clean(info.get("author"))
    if author and not JUNK_PDF_TITLE.match(author):
        out["authors"] = [a.strip() for a in re.split(r";|\band\b|,(?=\s*[A-Z][a-z]+\s)", author) if a.strip()]
    year = _year((info.get("creationDate") or "").replace("D:", ""))
    if year:
        out["year"] = year
    return out


def _llm_metadata(head: str, filename: str) -> dict:
    from .llm import chat_json

    user = f"File name: {filename}\n\nFIRST PAGES:\n{head}"
    try:
        data = json.loads(chat_json(METADATA_SYSTEM, user))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        log.warning("Metadata extraction via LLM failed for %s: %s", filename, exc)
        return {}


def extract_metadata(path: Path | None, markdown: str, filename: str, use_llm: bool = True) -> DocMeta:
    head = markdown[:8000]
    merged: dict = {"title": Path(filename).stem.replace("_", " ").strip()}
    if path is not None:
        merged.update(pdf_info(path))
    if use_llm:
        merged.update({k: v for k, v in _llm_metadata(head, filename).items() if v not in (None, "", [])})
    for key, value in find_identifiers(markdown[:20000]).items():
        if not merged.get(key):
            merged[key] = value
    merged["source"] = "auto"
    return DocMeta.from_dict(merged)


def author_short(meta: DocMeta) -> str:
    if not meta.authors:
        return ""
    surname = meta.authors[0].split()[-1]
    return surname if len(meta.authors) == 1 else (
        f"{surname} & {meta.authors[1].split()[-1]}" if len(meta.authors) == 2 else f"{surname} et al."
    )


def display_label(meta: DocMeta, fallback_title: str) -> str:
    title = meta.title or fallback_title
    bits = [b for b in (author_short(meta), str(meta.year) if meta.year else "") if b]
    return f"{title} ({', '.join(bits)})" if bits else title
