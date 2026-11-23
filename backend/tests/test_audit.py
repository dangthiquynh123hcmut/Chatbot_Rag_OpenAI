import unicodedata

from docx import Document

from app.audit import audit_file, check_text_quality, chunk_within
from app.converters import normalize_markdown
from app.hierarchy import parse_markdown
from app.references import parse_references


def _status(report, name):
    return next(c for c in report.checks if c.name == name)


def _make_docx(path, articles):
    d = Document()
    d.add_paragraph("CHAPTER I")
    d.add_paragraph("GENERAL PROVISIONS")
    for num, title, body in articles:
        d.add_paragraph().add_run(f"Article {num}. {title}").bold = True
        d.add_paragraph(body)
    d.save(path)


def test_nfd_input_is_normalized():
    nfd = unicodedata.normalize("NFD", "Article 3. Principles of café safety\n1. Ensure safety.\n2. Comply with the law.")
    root = parse_markdown(normalize_markdown(nfd), "x")
    art = next(n for n in root.iter() if n.kind == "article")
    assert art.number == "3" and len(art.children) == 2
    assert unicodedata.is_normalized("NFC", art.heading)
    assert parse_references("paragraph 2 of Article 3") == {"article": "3", "clause": "2"}


def test_text_quality():
    clean = " ".join(["the scope of this regulation applies to all agencies"] * 10)
    assert check_text_quality(clean).status == "pass"
    garbled = clean + " the companyâ€™s policy Ã© " * 3
    assert check_text_quality(garbled).status == "fail"
    assert check_text_quality("too short").status == "info"


def test_chunk_within_allows_repeated_heading():
    scope = "#### Article 1. Long\n\nParagraph one.\n\nParagraph two.\n\nParagraph three."
    assert chunk_within("# Article 1. Long\n\nParagraph three.", scope)
    assert not chunk_within("# Article 1. Long\n\nMissing paragraph.", scope)


def test_markdown_heading_does_not_adopt_numbered_sections():
    md = "## Abstract\ntext\n## 1 Introduction\nintro\n### Figure caption\ncap\n## 2 Background\nbg\n### 2.1 Detail\nd"
    root = parse_markdown(md, "paper")
    top = [n.display() for n in root.children]
    assert top == ["Abstract", "1. Introduction", "2. Background"]
    intro = root.children[1]
    assert [c.display() for c in intro.children] == ["Figure caption"]
    assert [c.display() for c in root.children[2].children] == ["2.1. Detail"]


def test_sentence_list_items_are_not_headings():
    md = (
        "## Chapter One\nIntro.\n1. Create a component that both modules depend on and move the shared classes there.\n"
        "2. Merge the two modules into one so that the cycle disappears completely from the graph.\n"
        "## Chapter Two\nMore text."
    )
    root = parse_markdown(md, "book")
    assert [n.display() for n in root.children] == ["Chapter One", "Chapter Two"]
    assert "2. Merge the two modules" in root.children[0].own_text


def test_audit_clean_document(tmp_path):
    p = tmp_path / "book.docx"
    _make_docx(p, [(1, "Scope", "This decree governs management."), (2, "Subjects", "Agencies and organizations.")])
    report = audit_file(p)
    assert _status(report, "Tính liên tục số thứ tự").status == "pass"
    assert _status(report, "Độ đầy đủ nội dung").status == "pass"
    assert _status(report, "Chunk").status == "pass"
    assert _status(report, "Unicode NFC").status == "pass"


def test_audit_detects_gaps_and_missed_headings(tmp_path):
    p = tmp_path / "broken.docx"
    _make_docx(
        p,
        [
            (1, "Scope", "Content one."),
            (2, "Subjects", "Content two."),
            (5, "Effect", "Content five."),
            (4, "Going back", "This line goes back in numbering so it is treated as body text."),
        ],
    )
    report = audit_file(p)
    seq = _status(report, "Tính liên tục số thứ tự")
    assert seq.status == "warn" and any("3, 4" in d for d in seq.details)
    missed = _status(report, "Tiêu đề bị bỏ sót")
    assert missed.status == "warn" and any("Article 4. Going back" in d for d in missed.details)
