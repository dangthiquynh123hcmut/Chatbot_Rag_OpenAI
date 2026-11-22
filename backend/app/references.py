from __future__ import annotations

import re

from .hierarchy import nfc, normalize_number, roman_to_int

REF_PATTERNS = [
    ("part", re.compile(r"\bphần\s+(thứ\s+\w+|[ivxlcdm]+|\d+)(?![\w.])", re.IGNORECASE)),
    ("chapter", re.compile(r"\bchương\s+([ivxlcdm]+|\d+)(?![\w.])", re.IGNORECASE)),
    ("subsection", re.compile(r"\btiểu\s+mục\s+([ivxlcdm]+|\d+)(?![\w.])", re.IGNORECASE)),
    ("section", re.compile(r"(?<!tiểu )\bmục\s+([ivxlcdm]+|\d+)(?![\w.])", re.IGNORECASE)),
    ("article", re.compile(r"\bđiều\s+(\d+[a-zđ]?)(?![\w.])", re.IGNORECASE)),
    ("clause", re.compile(r"\bkhoản\s+(\d+)(?![\w.])", re.IGNORECASE)),
    ("point", re.compile(r"\bđiểm\s+([a-zđ])(?![\w])", re.IGNORECASE)),
]
DOTTED_RE = re.compile(r"(?<![\w.])(\d{1,3}(?:\.\d{1,3})+)(?![\w]|\.\d)")
STRONG_KEYS = {"part", "chapter", "section", "subsection", "article", "num", "roman"}
STOPWORDS = {
    "là", "gì", "của", "và", "các", "những", "có", "được", "cho", "trong", "về", "theo", "nào",
    "thế", "như", "này", "đó", "với", "the", "of", "and", "what", "is", "a", "an", "to", "in",
    "quy", "định", "nội", "dung", "hỏi", "hãy", "cho", "biết", "tôi",
}


def _norm_ref(kind: str, raw: str) -> str:
    if kind == "point":
        return raw.lower()
    if re.fullmatch(r"[ivxlcdm]+", raw, re.IGNORECASE):
        v = roman_to_int(raw.upper())
        return str(v) if v else raw.lower()
    return normalize_number(raw)


def parse_references(query: str) -> dict[str, str]:
    query = nfc(query)
    refs: dict[str, str] = {}
    for kind, rx in REF_PATTERNS:
        m = rx.search(query)
        if m:
            refs[kind] = _norm_ref(kind, m.group(1))
    m = DOTTED_RE.search(query)
    if m:
        refs.pop("section", None)
        refs.pop("subsection", None)
        refs["num"] = normalize_number(m.group(1))
    return refs


def ref_variants(refs: dict[str, str]) -> list[dict[str, str]]:
    variants = [refs]
    alt_map = {"section": ["roman", "num"], "subsection": ["num"], "clause": ["num"], "part": ["roman"], "chapter": ["roman"]}
    for key, alts in alt_map.items():
        if key in refs:
            for alt in alts:
                v = {k: val for k, val in refs.items() if k != key}
                v[alt] = refs[key]
                variants.append(v)
    seen, out = set(), []
    for v in variants:
        sig = tuple(sorted(v.items()))
        if sig not in seen:
            seen.add(sig)
            out.append(v)
    return out
