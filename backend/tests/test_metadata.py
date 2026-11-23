import json
import re
import uuid
import zlib

import numpy as np
import pytest
from docx import Document

from app import metadata as md
from app import rag
from app.doc_resolver import CatalogEntry, resolve_documents
from app.evaluation import memory as mem
from app.hierarchy import parse_markdown
from app.metadata import DocMeta, clean_aliases, display_label, find_identifiers
from app.references import parse_references

ATTENTION = DocMeta.from_dict({
    "title": "Attention Is All You Need",
    "authors": ["Ashish Vaswani", "Noam Shazeer", "Niki Parmar"],
    "year": 2017,
    "doc_type": "paper",
    "arxiv_id": "1706.03762",
    "aliases": ["Transformer paper", "the", "Attention paper"],
})
CLEAN = DocMeta.from_dict({
    "title": "Clean Architecture: A Craftsman's Guide to Software Structure and Design",
    "authors": ["Robert C. Martin"],
    "year": 2017,
    "doc_type": "book",
    "isbn": "978-0-13-449416-6",
    "aliases": ["Uncle Bob's book", "Architecture"],
})
REFACTORING = DocMeta.from_dict({"title": "Refactoring", "authors": ["Martin Fowler"], "year": 2018, "doc_type": "book"})
CATALOG = [
    CatalogEntry("a", "NIPS-2017-attention", ATTENTION),
    CatalogEntry("c", "Clean Architecture", CLEAN),
    CatalogEntry("r", "refactoring", REFACTORING),
]


def _ids(query):
    return [m["id"] for m in resolve_documents(query, CATALOG)]


def test_docmeta_normalization():
    assert CLEAN.isbn == "9780134494166"
    assert CLEAN.aliases[0] == "Clean Architecture"
    assert "Architecture" not in CLEAN.aliases
    assert "the" not in ATTENTION.aliases
    bad = DocMeta.from_dict({"year": "n/a", "doc_type": "novel", "isbn": "123", "authors": [" ", "A B"]})
    assert bad.year is None and bad.doc_type == "other" and bad.isbn == "" and bad.authors == ["A B"]
    assert display_label(ATTENTION, "x") == "Attention Is All You Need (Vaswani et al., 2017)"
    assert display_label(REFACTORING, "x") == "Refactoring (Fowler, 2018)"
    assert display_label(DocMeta(), "fallback") == "fallback"


def test_alias_rules():
    assert clean_aliases(["BERT", "bert model", "Introduction", "ab", "Refactoring", "design", "ResNet"]) == [
        "BERT", "bert model", "ResNet",
    ]


def test_find_identifiers():
    text = "doi: 10.1145/3292500.3330701. Preprint arXiv:1706.03762v5 ISBN-13: 978-0-13-449416-6"
    assert find_identifiers(text) == {"doi": "10.1145/3292500.3330701", "arxiv_id": "1706.03762", "isbn": "9780134494166"}


def test_resolve_documents():
    assert _ids("In Clean Architecture, what is the Dependency Rule?") == ["c"]
    assert _ids("what does the transformer paper say about positional encoding") == ["a"]
    assert _ids("Vaswani et al. section 3.2 attention") == ["a"]
    assert _ids("arXiv 1706.03762 results") == ["a"]
    assert _ids("ISBN 9780134494166 chapter 5") == ["c"]
    assert _ids("Robert C. Martin on components") == ["c"]
    assert _ids("What does Refactoring say about long methods?") == ["r"]
    assert _ids("When is refactoring worth it?") == []
    assert _ids("Refactoring is risky without tests, right?") == []
    assert _ids("Is Architecture just about boxes?") == []
    assert _ids("What is software architecture?") == []
    assert set(_ids("Compare Clean Architecture with the Attention paper")) == {"a", "c"}


def test_english_labels_and_refs():
    root = parse_markdown("Chapter 3. Components\nText.\nPart II\nPRINCIPLES\nMore.", "book")
    assert [n.display() for n in root.children] == ["Chapter 3. Components", "Part II. PRINCIPLES"]
    vi = parse_markdown("Chương 2. Quy định\nNội dung.", "vi")
    assert vi.children[0].display() == "Chương 2. Quy định"
    assert parse_references("What does Chapter 5 of the book say?") == {"chapter": "5"}
    assert parse_references("Explain section 3.2 in the paper") == {"num": "3.2"}
    assert parse_references("Part II overview") == {"part": "2"}
    assert parse_references("see § 4 for details") == {"section": "4"}


def _fake_vec(text):
    v = np.zeros(256, dtype=np.float32)
    for tok in re.findall(r"\w+", text.lower()):
        v[zlib.crc32(tok.encode()) % 256] += 1.0
    return v / max(1.0, float(np.linalg.norm(v)))


def _doc(path, title, body):
    d = Document()
    d.add_heading(title, 1)
    d.add_heading("1 Introduction", 2)
    d.add_paragraph(body)
    d.save(path)


def test_retrieve_scopes_to_detected_document(tmp_path, monkeypatch):
    monkeypatch.setattr(mem, "embed_texts", lambda texts: [_fake_vec(t) for t in texts])
    monkeypatch.setattr(rag, "embed_query", _fake_vec)
    monkeypatch.setattr(rag, "rerank", lambda q, c, mode=None: c)
    _doc(tmp_path / "Deep_Learning_Notes.docx", "Deep Learning Notes", "Gradient descent updates weights using loss gradients.")
    _doc(tmp_path / "Optimization_Handbook.docx", "Optimization Handbook", "Gradient descent updates weights using loss gradients.")
    be = mem.MemoryBackend.from_paths(tmp_path, None)
    trace = rag.retrieve("In the Optimization Handbook, how does gradient descent work?", None, 5, be)
    assert trace.scope == "detected"
    assert [d["title"] for d in trace.documents_detected] == ["Optimization Handbook"]
    assert trace.sections and {c.document_filename for c in trace.sections} == {"Optimization_Handbook.docx"}
    trace_all = rag.retrieve("How does gradient descent work?", None, 5, be)
    assert trace_all.scope == "all"
    assert {c.document_filename for c in trace_all.sections} == {"Optimization_Handbook.docx", "Deep_Learning_Notes.docx"}
    chosen = be.document_ids_by_filename()["Deep_Learning_Notes.docx"]
    trace_sel = rag.retrieve("In the Optimization Handbook, how does gradient descent work?", [chosen], 5, be)
    assert trace_sel.scope == "selected"
    assert {c.document_filename for c in trace_sel.sections} == {"Deep_Learning_Notes.docx"}


def test_extract_metadata_merges_sources(monkeypatch):
    llm_out = {"title": "Attention Is All You Need", "authors": ["Ashish Vaswani"], "year": 2017,
               "doc_type": "paper", "aliases": ["Transformer paper"], "doi": ""}
    monkeypatch.setattr(md, "_llm_metadata", lambda head, filename: llm_out)
    meta = md.extract_metadata(None, "Title page ... arXiv:1706.03762v5 ...", "NIPS-2017.pdf")
    assert meta.title == "Attention Is All You Need" and meta.arxiv_id == "1706.03762"
    assert meta.source == "auto" and meta.aliases == ["Transformer paper"]
    no_llm = md.extract_metadata(None, "no ids here", "My_Book.docx", use_llm=False)
    assert no_llm.title == "My Book" and no_llm.authors == []


def test_rag_result_reports_scope(monkeypatch):
    trace = rag.RetrievalTrace(query="q", refs={})
    trace.documents_detected = [{"id": str(uuid.uuid4()), "title": "T", "matched": "T", "match_kind": "title"}]
    trace.scope = "detected"
    res = rag.generate_answer(trace, backend=object())
    assert res["scope"] == "detected" and res["documents_detected"][0]["title"] == "T"
    assert json.dumps(res)
