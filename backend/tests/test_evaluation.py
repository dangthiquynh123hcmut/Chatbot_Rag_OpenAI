import json
import re
import zlib

import numpy as np
import pytest
from docx import Document

from app import llm, rag
from app.cli.evaluation import memory as mem
from app.cli.evaluation import metrics as M
from app.cli.evaluation import runner
from app.cli.evaluation.dataset import EvalItem, load_dataset, save_dataset
from app.cli.evaluation.generate import generate_dataset, reference_question
from app.cli.evaluation.report import evaluate_thresholds, write_report


def _fake_vec(text: str) -> np.ndarray:
    v = np.zeros(256, dtype=np.float32)
    for tok in re.findall(r"\w+", text.lower()):
        v[zlib.crc32(tok.encode("utf-8")) % 256] += 1.0
    return v / max(1.0, float(np.linalg.norm(v)))


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(mem, "embed_texts", lambda texts: [_fake_vec(t) for t in texts])
    monkeypatch.setattr(rag, "embed_query", _fake_vec)

    def fake_chat(system, user, temperature=0.0, model=None):
        if "judge" in system:
            return json.dumps({"faithfulness": 1.0, "correctness": "correct", "reason": "ok"})
        sources = re.findall(r"\[SOURCE (\d+)\][^\n]*\nLocation: ([^\n]*)\n\n(.*?)(?=\n\n========|\Z)", user, re.S)
        question = user.split("\n", 1)[0].lower()
        if "mars" in question:
            return json.dumps({"found": False, "source": 0, "quote": "", "answer": "Not found."})
        idx, _, body = sources[0]
        line = next((ln for ln in body.splitlines() if ln and not ln.startswith("#")), body)
        return json.dumps({"found": True, "source": int(idx), "quote": line, "answer": f"According to {line}"})

    monkeypatch.setattr(rag, "chat_json", fake_chat)
    monkeypatch.setattr(llm, "chat_json", fake_chat)


@pytest.fixture
def docs(tmp_path):
    d = Document()
    d.add_paragraph("CHAPTER I")
    d.add_paragraph("GENERAL PROVISIONS")
    d.add_paragraph().add_run("Article 1. Scope of regulation").bold = True
    d.add_paragraph("This decree governs quality management of construction works.")
    d.add_paragraph().add_run("Article 2. Survey tasks").bold = True
    d.add_paragraph("1. Survey tasks are prepared for each design step.")
    d.add_paragraph("2. The task content includes:")
    d.add_paragraph("a) Purpose of the survey;")
    d.add_paragraph("b) Scope of the construction survey;")
    d.add_paragraph().add_run("Article 3. Survey supervision").bold = True
    d.add_paragraph("The project owner is responsible for supervising the survey work.")
    folder = tmp_path / "docs"
    folder.mkdir()
    d.save(folder / "decree.docx")
    return folder


def test_path_relation_and_ranks():
    gold = ["Chapter I", "Article 2", "Paragraph 2"]
    assert M.path_relation(gold, gold) == "exact"
    assert M.path_relation(["Chapter I", "Article 2"], gold) == "ancestor"
    assert M.path_relation(gold + ["Point b"], gold) == "descendant"
    assert M.path_relation(["Chapter I", "Article 3"], gold) == "none"
    ranks = [0, 2, None, 6]
    assert M.recall_at(ranks, 1) == 0.25 and M.recall_at(ranks, 5) == 0.5
    assert M.mrr(ranks) == pytest.approx((1 + 1 / 3 + 1 / 7) / 4)
    assert M.token_f1("the owner supervises the survey", "The owner is responsible for supervising the survey") == pytest.approx(8 / 13)


def test_reference_question_templates(docs):
    _, root = mem.load_document(docs / "decree.docx")
    point = next(n for n in root.iter() if n.kind == "point" and n.number == "b")
    assert reference_question(point) == "What does point b of paragraph 2 of Article 2 provide?"


def test_memory_backend_search(docs, offline, tmp_path):
    be = mem.MemoryBackend.from_paths(docs, tmp_path / "cache")
    assert (tmp_path / "cache").exists() and any((tmp_path / "cache").iterdir())
    hits = be.lookup_by_references({"article": "2", "clause": "2"}, None)
    assert hits and hits[0].path[-1] == "Paragraph 2"
    kw = be.keyword_search("project owner supervising survey", None, 3)
    assert kw[0].path[-1] == "Article 3. Survey supervision"
    vec = be.vector_search(_fake_vec("project owner supervising the survey work"), None, 3)
    assert vec[0].path[-1] == "Article 3. Survey supervision"


def test_end_to_end_evaluation(docs, offline, tmp_path):
    items = generate_dataset(docs, sections_per_doc=10, use_llm=False)
    assert items and all(i.type == "reference" for i in items)
    items.append(EvalItem(id="x-1", document="decree.docx", question="Who supervises the survey work?",
                          type="semantic", gold_path=["Chapter I. GENERAL PROVISIONS", "Article 3. Survey supervision"],
                          gold_quote="The project owner is responsible for supervising the survey work."))
    items.append(EvalItem(id="x-2", document="decree.docx", question="What are the rules on Mars?",
                          type="unanswerable", answerable=False))
    path = tmp_path / "ds.jsonl"
    save_dataset(items, path)
    items = load_dataset(path)
    be = mem.MemoryBackend.from_paths(docs, None)
    ret = runner.run_retrieval(items, be, reranker="none", workers=2)
    assert ret["summary"]["branches"]["pipeline"]["recall@5"] == 1.0
    assert ret["summary"]["branches"]["reference"]["exact@1"] == 1.0
    e2e = runner.run_e2e(items, be, judge=True, workers=2)
    s = e2e["summary"]
    assert s["abstention"] == 1.0
    assert s["quote_verified"] == 1.0
    assert s["faithfulness"] == 1.0
    assert s["granularity"].get("wrong", 0) == 0 and s["granularity"].get("no_answer", 0) == 0
    md_path, checks = write_report(tmp_path / "reports", str(path), "memory", ret, e2e)
    text = md_path.read_text(encoding="utf-8")
    assert "Truy hồi" in text and "End-to-end" in text
    assert any(c["metric"] == "abstention" and c["pass"] for c in checks)


def test_thresholds_flag_failures():
    summary = {"e2e": {"section_exact": 0.5, "abstention": 0.9}}
    res = {c["metric"]: c["pass"] for c in evaluate_thresholds(summary)}
    assert res == {"section_exact": False, "abstention": True}


def test_judge_falls_back_when_model_unavailable(monkeypatch):
    from botocore.exceptions import ClientError

    from app.config import get_settings

    s = get_settings()
    calls = []

    def fake_chat(system, user, temperature=0.0, model=None):
        calls.append(model)
        if model == s.judge_model and model != s.active_llm_model:
            raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "not available"}}, "Converse")
        return '{"ok": true}'

    monkeypatch.setattr(llm, "chat_json", fake_chat)
    monkeypatch.setattr(llm, "_UNAVAILABLE_MODELS", set())
    assert llm.judge_chat_json("s", "u") == '{"ok": true}'
    assert llm.effective_judge_model() == s.active_llm_model
    assert llm.judge_chat_json("s", "u") == '{"ok": true}'
    if s.judge_model != s.active_llm_model:
        assert calls == [s.judge_model, s.active_llm_model, s.active_llm_model]


def test_generation_helpers(docs):
    import random

    from app.cli.evaluation.generate import _candidate_sections, _stratified_sample, refine_gold

    _, root = mem.load_document(docs / "decree.docx")
    article = next(n for n in root.iter() if n.kind == "article" and n.number == "2")
    assert refine_gold(article, "b) Scope of the construction survey;").path[-1] == "Point b"
    assert refine_gold(article, "").path == article.path
    picked = _stratified_sample(_candidate_sections(root), 20, random.Random(1))
    ids = {id(n) for n in picked}
    for n in picked:
        assert not ({id(a) for a in n.ancestors()} & ids)
