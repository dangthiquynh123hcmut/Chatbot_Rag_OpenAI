from __future__ import annotations

import hashlib
import random
import uuid

import numpy as np

from .audit import AuditReport, Check, _squash, _status, chunk_within
from .config import get_settings
from .db import connection
from .retrieval import fetch_subtree


def _documents(doc_id: uuid.UUID | None) -> list[dict]:
    with connection() as conn:
        if doc_id:
            return conn.execute("SELECT id, title, filename FROM documents WHERE id=%s", (doc_id,)).fetchall()
        return conn.execute(
            "SELECT id, title, filename FROM documents WHERE status='ready' ORDER BY created_at"
        ).fetchall()


def _chunk_rows(doc_id: uuid.UUID) -> list[dict]:
    with connection() as conn:
        return conn.execute(
            """SELECT c.id, c.content, c.embedding, c.embedding_model, s.full_md, s.path, s.kind
               FROM chunks c JOIN sections s ON s.id = c.section_id WHERE c.document_id=%s""",
            (doc_id,),
        ).fetchall()


def _to_array(value) -> np.ndarray:
    if hasattr(value, "to_numpy"):
        value = value.to_numpy()
    return np.asarray(value, dtype=np.float32)


def check_vectors(rows: list[dict]) -> list[Check]:
    s = get_settings()
    expected_model = f"{s.llm_provider}:{s.active_embedding_model}:{s.embedding_dim}"
    vecs = [_to_array(r["embedding"]) for r in rows]
    dims = {v.shape[0] for v in vecs}
    bad = [i for i, v in enumerate(vecs) if not np.all(np.isfinite(v))]
    zero = [i for i, v in enumerate(vecs) if float(np.linalg.norm(v)) < 1e-6]
    finite = [v for v in vecs if np.all(np.isfinite(v))]
    norms = np.array([float(np.linalg.norm(v)) for v in finite]) if finite else np.array([0.0])
    checks = []

    dim_ok = dims == {s.embedding_dim}
    checks.append(Check("Số chiều vector", "pass" if dim_ok else "fail",
                        f"{sorted(dims)} (cấu hình EMBEDDING_DIM={s.embedding_dim})"))
    details = [f"Chunk lỗi NaN/Inf: {' > '.join(rows[i]['path'])}" for i in bad]
    details += [f"Vector toàn 0: {' > '.join(rows[i]['path'])}" for i in zero]
    checks.append(Check("Giá trị vector", "fail" if details else "pass",
                        f"{len(bad)} NaN/Inf, {len(zero)} vector rỗng", details))

    normalized_expected = s.llm_provider == "bedrock" and "titan-embed-text-v2" in s.active_embedding_model
    spread = float(np.max(np.abs(norms - 1.0))) if len(norms) else 0.0
    norm_status = _status(spread, 0.02, 0.1, False) if normalized_expected else "info"
    checks.append(Check("Độ dài vector (norm)", norm_status,
                        f"min {norms.min():.4f}, max {norms.max():.4f}, TB {norms.mean():.4f}",
                        metrics={"norm_min": float(norms.min()), "norm_max": float(norms.max())}))

    models = {r["embedding_model"] for r in rows}
    if models == {expected_model}:
        m_status, m_sum = "pass", expected_model
    elif None in models and len(models) == 1:
        m_status, m_sum = "warn", "Chưa ghi embedding_model (dữ liệu nạp trước khi có cột này) – nên xử lý lại"
    else:
        m_status, m_sum = "fail", f"Lẫn nhiều model {sorted(map(str, models))}, cấu hình hiện tại {expected_model}"
    checks.append(Check("Nhất quán model embedding", m_status, m_sum))

    by_hash: dict[str, list[int]] = {}
    for i, v in enumerate(vecs):
        by_hash.setdefault(hashlib.sha1(np.round(v, 5).tobytes()).hexdigest(), []).append(i)
    dup_details = []
    for idxs in by_hash.values():
        contents = {_squash(rows[i]["content"]) for i in idxs}
        if len(idxs) > 1 and len(contents) > 1:
            dup_details.append(" | ".join(" > ".join(rows[i]["path"]) for i in idxs[:3]))
    checks.append(Check("Vector trùng cho nội dung khác nhau", "fail" if dup_details else "pass",
                        f"{len(dup_details)} nhóm", dup_details))

    outside = [" > ".join(r["path"]) for r in rows if not chunk_within(r["content"], r["full_md"] or "")]
    checks.append(Check("Chunk nằm gọn trong mục", "fail" if outside else "pass",
                        f"{len(outside)} chunk vượt ra ngoài mục", outside))
    return checks


def check_self_retrieval(doc_id: uuid.UUID, sample: int, seed: int = 42) -> Check:
    from .llm import embed_query

    with connection() as conn:
        sections = conn.execute(
            """SELECT id, title, path FROM sections
               WHERE document_id=%s AND kind NOT IN ('document','clause','point')
                 AND title IS NOT NULL AND length(title) >= 6""",
            (doc_id,),
        ).fetchall()
    if not sections:
        return Check("Self-retrieval (tiêu đề → mục)", "info", "Không có mục có tiêu đề để thử")
    random.Random(seed).shuffle(sections)
    picked = sections[:sample]
    hit1 = hit5 = 0
    rr = 0.0
    misses = []
    for sec in picked:
        family = {row["id"] for row in fetch_subtree(sec["id"])}
        with connection() as conn:
            top = conn.execute(
                """SELECT c.section_id FROM chunks c WHERE c.document_id=%s
                   ORDER BY c.embedding <=> %s LIMIT 5""",
                (doc_id, embed_query(sec["title"])),
            ).fetchall()
        ranks = [i for i, r in enumerate(top) if r["section_id"] in family]
        if ranks:
            hit5 += 1
            hit1 += ranks[0] == 0
            rr += 1.0 / (ranks[0] + 1)
        else:
            misses.append(f"'{sec['title'][:60]}' → không có trong top 5 ({' > '.join(sec['path'])})")
    n = len(picked)
    h1, h5, mrr = hit1 / n, hit5 / n, rr / n
    return Check("Self-retrieval (tiêu đề → mục)", _status(h5, 0.8, 0.5),
                 f"{n} mẫu: Hit@1 {h1:.0%}, Hit@5 {h5:.0%}, MRR {mrr:.3f}", misses,
                 {"hit@1": round(h1, 4), "hit@5": round(h5, 4), "mrr": round(mrr, 4), "n": n})


def audit_database(doc_id: uuid.UUID | None = None, self_retrieval: int = 0) -> list[AuditReport]:
    reports = []
    for d in _documents(doc_id):
        rows = _chunk_rows(d["id"])
        if not rows:
            reports.append(AuditReport(d["filename"], d["title"], [Check("Chunk", "fail", "Không có chunk nào")], {}))
            continue
        checks = check_vectors(rows)
        if self_retrieval > 0:
            checks.append(check_self_retrieval(d["id"], self_retrieval))
        reports.append(AuditReport(d["filename"], d["title"], checks, {"document_id": str(d["id"]), "chunks": len(rows)}))
    return reports
