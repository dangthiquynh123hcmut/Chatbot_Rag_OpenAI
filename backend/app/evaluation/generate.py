from __future__ import annotations

import json
import logging
import random
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..config import get_settings
from ..audit import VIET_MARKED
from ..hierarchy import INLINE_KINDS, Node
from ..llm import judge_chat_json
from ..rag import normalize_for_match, quote_in
from .dataset import EvalItem
from .memory import list_documents, load_document

log = logging.getLogger(__name__)

QUESTION_SYSTEM = """Bạn tạo bộ câu hỏi đánh giá cho hệ thống tra cứu tài liệu (RAG).
Cho một MỤC trong tài liệu, hãy tạo câu hỏi mà CHỈ mục này trả lời được.
Yêu cầu:
- Viết câu hỏi bằng cùng ngôn ngữ với tài liệu.
- "semantic": diễn đạt lại bằng từ ngữ khác, KHÔNG nhắc số hiệu/tiêu đề mục, không chép nguyên câu trong văn bản.
- "factoid": hỏi một chi tiết cụ thể (con số, thời hạn, đối tượng, điều kiện, ô trong bảng).
- "quote": trích NGUYÊN VĂN một đoạn ngắn (1-2 câu) trong mục chứa câu trả lời, giữ đúng từng chữ.
- "answer": câu trả lời ngắn, đúng theo mục.
- Không tạo câu hỏi cần kiến thức bên ngoài.
Trả về JSON: {"questions": [{"type": "semantic|factoid", "question": "...", "answer": "...", "quote": "..."}]}"""

UNANSWERABLE_SYSTEM = """Bạn tạo câu hỏi KHÔNG THỂ trả lời từ tài liệu, để kiểm tra hệ thống RAG có biết từ chối hay không.
Cho tên tài liệu và danh sách mục của nó, hãy tạo câu hỏi nghe hợp lý, cùng chủ đề, cùng ngôn ngữ với tài liệu,
nhưng tài liệu chắc chắn KHÔNG chứa câu trả lời (ví dụ hỏi về mức phạt, số liệu, quốc gia, thời điểm không được đề cập).
Trả về JSON: {"questions": ["...", "..."]}"""

KIND_PRIORITY = {"point": 0, "clause": 1, "article": 2, "num": 3, "roman": 4, "md": 5, "subsection": 6, "section": 7}


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", Path(name).stem.lower()).strip("-")[:30] or "doc"


def _candidate_sections(root: Node) -> list[Node]:
    out = []
    for n in root.iter():
        if n.kind == "document":
            continue
        text = n.full_markdown()
        body_len = len(text) - len(n.heading)
        if body_len < 40 or len(text) > 6000:
            continue
        has_titled_children = any(c.kind not in INLINE_KINDS for c in n.children)
        if has_titled_children and len(text) > 2500:
            continue
        out.append(n)
    return out


def _stratified_sample(nodes: list[Node], k: int, rng: random.Random) -> list[Node]:
    groups: dict[str, list[Node]] = defaultdict(list)
    for n in nodes:
        groups[n.kind].append(n)
    for g in groups.values():
        rng.shuffle(g)
    order = sorted(groups, key=lambda kind: KIND_PRIORITY.get(kind, 9))
    picked: list[Node] = []
    taken: set[int] = set()
    while len(picked) < k and any(groups[o] for o in order):
        for o in order:
            while groups[o] and len(picked) < k:
                n = groups[o].pop()
                family = {id(a) for a in n.ancestors()} | {id(d) for d in n.iter()}
                if family & taken:
                    continue
                picked.append(n)
                taken.add(id(n))
                break
    return picked


def document_language(root: Node) -> str:
    words = re.findall(r"[^\W\d_]+", " ".join(n.own_text for n in root.iter()).lower())
    if not words:
        return "tiếng Việt"
    marked = sum(1 for w in words if any(ch in VIET_MARKED for ch in w))
    return "tiếng Việt" if marked / len(words) > 0.15 else "tiếng Anh (English)"


def refine_gold(node: Node, quote: str) -> Node:
    if not quote.strip():
        return node
    best = node
    for d in node.iter():
        if d.depth > best.depth and quote_in(quote, d.full_markdown()):
            best = d
    return best


def reference_question(node: Node) -> str | None:
    r = node.refs
    if "article" in r:
        parts = []
        if "point" in r:
            parts.append(f"Điểm {r['point']}")
        if "clause" in r:
            parts.append(f"khoản {r['clause']}" if parts else f"Khoản {r['clause']}")
        parts.append(f"Điều {r['article']}")
        return " ".join(parts) + " quy định gì?"
    if node.kind == "num" and "num" in r and "." in r["num"]:
        return f"Mục {r['num']} nói về nội dung gì?"
    return None


def _overlap(question: str, text: str) -> float:
    q = set(re.findall(r"\w+", question.lower()))
    t = set(re.findall(r"\w+", text.lower()))
    return len(q & t) / max(1, len(q))


def _llm_questions(title: str, node: Node, per_section: int, language: str) -> list[dict]:
    text = node.full_markdown()
    user = (
        f"Tài liệu: {title}\nVị trí: {' > '.join(node.path)}\n"
        f"Ngôn ngữ bắt buộc của câu hỏi và đáp án: {language}\n"
        f"Số câu cần tạo: {per_section} (trộn semantic và factoid)\n\nNỘI DUNG MỤC:\n{text}"
    )
    try:
        data = json.loads(judge_chat_json(QUESTION_SYSTEM, user, temperature=0.4))
    except Exception as exc:
        log.warning("Question generation failed for %s: %s", node.path, exc)
        return []
    out = []
    for q in data.get("questions", [])[:per_section]:
        question = (q.get("question") or "").strip()
        quote = (q.get("quote") or "").strip()
        qtype = q.get("type") if q.get("type") in ("semantic", "factoid") else "semantic"
        if len(question) < 8:
            continue
        if quote and not quote_in(quote, text):
            log.info("Drop question with unverifiable quote: %s", question)
            continue
        if qtype == "semantic" and _overlap(question, text) > 0.85:
            log.info("Drop too-literal question: %s", question)
            continue
        out.append({"type": qtype, "question": question, "answer": (q.get("answer") or "").strip(), "quote": quote})
    return out


def _unanswerable(title: str, root: Node, n: int, language: str) -> list[str]:
    if n <= 0:
        return []
    outline = "\n".join(" > ".join(x.path) for x in root.iter() if x.kind not in ("document", "clause", "point"))[:6000]
    user = f"Tài liệu: {title}\nNgôn ngữ bắt buộc của câu hỏi: {language}\nSố câu cần tạo: {n}\n\nDANH SÁCH MỤC:\n{outline}"
    try:
        data = json.loads(judge_chat_json(UNANSWERABLE_SYSTEM, user, temperature=0.6))
    except Exception as exc:
        log.warning("Unanswerable generation failed for %s: %s", title, exc)
        return []
    return [q.strip() for q in data.get("questions", []) if isinstance(q, str) and len(q.strip()) > 8][:n]


def generate_dataset(
    target: Path,
    sections_per_doc: int = 20,
    questions_per_section: int = 2,
    unanswerable_per_doc: int = 5,
    seed: int = 7,
    use_llm: bool = True,
) -> list[EvalItem]:
    settings = get_settings()
    rng = random.Random(seed)
    items: list[EvalItem] = []
    for path in list_documents(target):
        title, root = load_document(path)
        slug = _slug(path.name)
        picked = _stratified_sample(_candidate_sections(root), sections_per_doc, rng)
        language = document_language(root)
        counter = 0
        seen_questions: set[str] = set()

        def add(**kw) -> None:
            nonlocal counter
            key = normalize_for_match(kw["question"])
            if key in seen_questions:
                log.info("Drop duplicate question: %s", kw["question"])
                return
            seen_questions.add(key)
            counter += 1
            items.append(EvalItem(id=f"{slug}-{counter:04d}", document=path.name, **kw))

        for node in picked:
            rq = reference_question(node)
            if rq:
                add(question=rq, type="reference", gold_path=node.path, gold_refs=node.refs, section_kind=node.kind)
        if use_llm:
            with ThreadPoolExecutor(max_workers=settings.bedrock_max_workers) as pool:
                results = list(pool.map(lambda n: _llm_questions(title, n, questions_per_section, language), picked))
            for node, qs in zip(picked, results):
                for q in qs:
                    gold = refine_gold(node, q["quote"])
                    add(question=q["question"], type=q["type"], gold_path=gold.path, gold_refs=gold.refs,
                        gold_quote=q["quote"], reference_answer=q["answer"], section_kind=gold.kind)
            for q in _unanswerable(title, root, unanswerable_per_doc, language):
                add(question=q, type="unanswerable", answerable=False)
        log.info("%s: %d sections sampled, %d items", path.name, len(picked), counter)
    return items
