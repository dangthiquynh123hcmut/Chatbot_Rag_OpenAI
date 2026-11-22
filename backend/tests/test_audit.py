import unicodedata

from docx import Document

from app.audit import audit_file, chunk_within, is_valid_syllable
from app.converters import normalize_markdown
from app.hierarchy import parse_markdown
from app.references import parse_references


def _status(report, name):
    return next(c for c in report.checks if c.name == name)


def _make_docx(path, articles):
    d = Document()
    d.add_paragraph("CHƯƠNG I")
    d.add_paragraph("QUY ĐỊNH CHUNG")
    for num, title, body in articles:
        d.add_paragraph().add_run(f"Điều {num}. {title}").bold = True
        d.add_paragraph(body)
    d.save(path)


def test_nfd_input_is_normalized():
    nfd = unicodedata.normalize("NFD", "Điều 3. Nguyên tắc\n1. Bảo đảm an toàn.\n2. Tuân thủ pháp luật.")
    root = parse_markdown(normalize_markdown(nfd), "x")
    art = next(n for n in root.iter() if n.kind == "article")
    assert art.number == "3" and len(art.children) == 2
    assert unicodedata.is_normalized("NFC", art.heading)
    assert parse_references(unicodedata.normalize("NFD", "Khoản 2 Điều 3")) == {"article": "3", "clause": "2"}


def test_vietnamese_syllables():
    for w in ["nghiêng", "người", "quốc", "khuyết", "đường", "giữa", "của", "oanh"]:
        assert is_valid_syllable(w), w
    for w in ["thµnh", "nguoòi", "ááb", "xyzq"]:
        assert not is_valid_syllable(w), w


def test_chunk_within_allows_repeated_heading():
    scope = "#### Điều 1. Dài\n\nĐoạn một.\n\nĐoạn hai.\n\nĐoạn ba."
    assert chunk_within("# Điều 1. Dài\n\nĐoạn ba.", scope)
    assert not chunk_within("# Điều 1. Dài\n\nĐoạn không có.", scope)


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
    p = tmp_path / "sach.docx"
    _make_docx(p, [(1, "Phạm vi", "Nghị định này quy định về quản lý."), (2, "Đối tượng", "Cơ quan, tổ chức.")])
    report = audit_file(p)
    assert _status(report, "Tính liên tục số thứ tự").status == "pass"
    assert _status(report, "Độ đầy đủ nội dung").status == "pass"
    assert _status(report, "Chunk").status == "pass"
    assert _status(report, "Unicode NFC").status == "pass"


def test_audit_detects_gaps_and_missed_headings(tmp_path):
    p = tmp_path / "loi.docx"
    _make_docx(
        p,
        [
            (1, "Phạm vi", "Nội dung một."),
            (2, "Đối tượng", "Nội dung hai."),
            (5, "Hiệu lực", "Nội dung năm."),
            (4, "Lặp lùi", "Dòng này lùi số nên bị coi là thân bài."),
        ],
    )
    report = audit_file(p)
    seq = _status(report, "Tính liên tục số thứ tự")
    assert seq.status == "warn" and any("3, 4" in d for d in seq.details)
    missed = _status(report, "Tiêu đề bị bỏ sót")
    assert missed.status == "warn" and any("Điều 4. Lặp lùi" in d for d in missed.details)
