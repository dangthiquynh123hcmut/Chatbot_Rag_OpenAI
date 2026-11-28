from __future__ import annotations

import math
import re
from collections import Counter
from typing import Sequence

from ...hierarchy import nfc


def _norm_label(s: str) -> str:
    return re.sub(r"\s+", " ", nfc(s)).strip().lower()


def path_relation(retrieved: Sequence[str], gold: Sequence[str]) -> str:
    r = [_norm_label(x) for x in retrieved]
    g = [_norm_label(x) for x in gold]
    if not r or not g:
        return "none"
    if r == g:
        return "exact"
    if len(r) < len(g) and g[: len(r)] == r:
        return "ancestor"
    if len(g) < len(r) and r[: len(g)] == g:
        return "descendant"
    return "none"


def first_rank(ranked: Sequence[tuple[str, Sequence[str]]], document: str, gold: Sequence[str], accept: set[str]) -> int | None:
    for i, (doc, path) in enumerate(ranked):
        if doc == document and path_relation(path, gold) in accept:
            return i
    return None


def recall_at(ranks: Sequence[int | None], k: int) -> float:
    return sum(1 for r in ranks if r is not None and r < k) / max(1, len(ranks))


def mrr(ranks: Sequence[int | None], k: int = 10) -> float:
    return sum(1.0 / (r + 1) for r in ranks if r is not None and r < k) / max(1, len(ranks))


def ndcg(ranks: Sequence[int | None], k: int = 10) -> float:
    return sum(1.0 / math.log2(r + 2) for r in ranks if r is not None and r < k) / max(1, len(ranks))


def token_f1(pred: str, gold: str) -> float:
    p = re.findall(r"\w+", nfc(pred).lower())
    g = re.findall(r"\w+", nfc(gold).lower())
    if not p or not g:
        return 0.0
    common = sum((Counter(p) & Counter(g)).values())
    if common == 0:
        return 0.0
    precision, recall = common / len(p), common / len(g)
    return 2 * precision * recall / (precision + recall)


def percentile(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, max(0, int(round(q / 100 * (len(s) - 1)))))
    return s[idx]


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0
