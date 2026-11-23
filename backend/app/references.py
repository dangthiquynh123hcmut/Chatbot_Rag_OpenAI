from __future__ import annotations

import re

from .hierarchy import nfc, normalize_number, roman_to_int

REF_PATTERNS = [
    ("part", re.compile(r"\bpart\s+([ivxlcdm]+|\d+)(?![\w.])", re.IGNORECASE)),
    ("chapter", re.compile(r"\b(?:chapter|chap\.|ch\.)\s*([ivxlcdm]+|\d+)(?![\w.])", re.IGNORECASE)),
    ("subsection", re.compile(r"\bsubsection\s+([ivxlcdm]+|\d+)(?![\w.])", re.IGNORECASE)),
    ("section", re.compile(r"(?:\bsection|\bsec\.|§)\s*([ivxlcdm]+|\d+)(?![\w.])", re.IGNORECASE)),
    ("article", re.compile(r"\b(?:article|art\.)\s*(\d+[a-z]?)(?![\w.])", re.IGNORECASE)),
    ("clause", re.compile(r"\b(?:paragraph|para\.|clause)\s*(\d+)(?![\w.])", re.IGNORECASE)),
    ("point", re.compile(r"\bpoint\s+([a-z])(?![\w])", re.IGNORECASE)),
]
DOTTED_RE = re.compile(r"(?<![\w.])(\d{1,3}(?:\.\d{1,3})+)(?![\w]|\.\d)")
STRONG_KEYS = {"part", "chapter", "section", "subsection", "article", "num", "roman"}
STOPWORDS = {
    "the", "of", "and", "what", "is", "a", "an", "to", "in",
    "about", "above", "after", "again", "all", "also", "am", "are", "as", "at", "be", "been", "being",
    "between", "both", "but", "by", "can", "could", "did", "do", "does", "doing", "during", "each", "explain",
    "for", "from", "further", "had", "has", "have", "having", "he", "her", "here", "hers", "him", "his",
    "how", "i", "if", "into", "it", "its", "itself", "me", "more", "most", "my", "no", "nor", "not", "now",
    "on", "once", "only", "or", "other", "our", "ours", "out", "over", "own", "please", "same", "say", "says",
    "she", "should", "so", "some", "such", "tell", "than", "that", "their", "theirs", "them", "then", "there",
    "these", "they", "this", "those", "through", "too", "under", "until", "up", "very", "was", "we", "were",
    "when", "where", "which", "while", "who", "whom", "why", "will", "with", "would", "you", "your", "yours",
    "according", "describe", "described", "mean", "means", "book", "paper", "author", "authors",
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
        if m and kind not in refs:
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
