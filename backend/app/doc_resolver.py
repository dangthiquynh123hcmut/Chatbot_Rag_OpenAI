from __future__ import annotations

import re
from dataclasses import dataclass, field

from .hierarchy import nfc
from .metadata import DocMeta, clean_aliases

MIN_PHRASE_CHARS = 4


@dataclass
class CatalogEntry:
    id: str
    title: str
    meta: DocMeta = field(default_factory=DocMeta)


@dataclass
class Phrase:
    text: str
    kind: str
    case_sensitive: bool = False


def _norm(text: str) -> str:
    return " " + re.sub(r"[^\w]+", " ", nfc(text).lower()).strip() + " "


def _phrases(entry: CatalogEntry) -> list[Phrase]:
    meta = entry.meta
    out: list[Phrase] = []
    for t in {entry.title, meta.title}:
        if not t or len(t) < MIN_PHRASE_CHARS:
            continue
        single = len(t.split()) == 1
        if single and not any(c.isupper() for c in t):
            continue
        out.append(Phrase(t, "title", case_sensitive=single))
    for alias in clean_aliases(meta.aliases, meta.title or entry.title, meta.short_title):
        single = len(alias.split()) == 1
        acronym = single and sum(1 for c in alias if c.isupper()) >= 2
        out.append(Phrase(alias, "alias", case_sensitive=single and not acronym))
    for ident, kind in ((meta.doi, "doi"), (meta.arxiv_id, "arxiv"), (meta.isbn, "isbn")):
        if ident:
            out.append(Phrase(ident, kind))
    if meta.authors:
        surname = meta.authors[0].split()[-1]
        if len(surname) >= 3:
            out.append(Phrase(f"{surname} et al", "author"))
            if meta.year:
                out.append(Phrase(f"{surname} {meta.year}", "author"))
                out.append(Phrase(f"{surname} et al {meta.year}", "author"))
        for name in meta.authors[:3]:
            if len(name.split()) >= 2:
                out.append(Phrase(name, "author"))
    return out


def _contains(query: str, phrase: Phrase) -> bool:
    if phrase.kind == "isbn":
        return phrase.text in re.sub(r"[^\dX]", "", query.upper())
    if phrase.case_sensitive:
        for m in re.finditer(rf"(?<![\w]){re.escape(phrase.text)}(?![\w])", query):
            before = query[: m.start()].rstrip(" \"'“‘*_(")
            if before and not before.endswith((".", "?", "!", ":")):
                return True
        return False
    p = _norm(phrase.text)
    return len(p.strip()) >= 3 and p in _norm(query)


def resolve_documents(query: str, catalog: list[CatalogEntry]) -> list[dict]:
    matches: list[dict] = []
    for entry in catalog:
        best: Phrase | None = None
        for phrase in _phrases(entry):
            if _contains(query, phrase) and (best is None or len(phrase.text) > len(best.text)):
                best = phrase
        if best is not None:
            matches.append({"id": entry.id, "title": entry.meta.title or entry.title,
                            "matched": best.text, "match_kind": best.kind})
    matches.sort(key=lambda m: -len(m["matched"]))
    return matches
