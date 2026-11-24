import pytest

docling_core = pytest.importorskip("docling_core")

from docling_core.types.doc import DoclingDocument, TableCell, TableData

from app.docling_pdf import JUNK_HEADING_RE, norm_title, table_to_markdown
from app.hierarchy import build_chunks, embedding_text, parse_markdown, split_paragraphs


def _cell(text, row, col, row_span=1, col_span=1, header=False):
    return TableCell(
        text=text, row_span=row_span, col_span=col_span, column_header=header,
        start_row_offset_idx=row, end_row_offset_idx=row + row_span,
        start_col_offset_idx=col, end_col_offset_idx=col + col_span,
    )


def test_merged_header_table_is_flattened():
    cells = [
        _cell("Model", 0, 0, row_span=2, header=True),
        _cell("BLEU", 0, 1, col_span=2, header=True),
        _cell("EN-DE", 1, 1, header=True),
        _cell("EN-FR", 1, 2, header=True),
        _cell("ByteNet", 2, 0), _cell("23.75", 2, 1), _cell("", 2, 2),
        _cell("Transformer | big", 3, 0), _cell("28.4", 3, 1), _cell("41.8", 3, 2),
    ]
    doc = DoclingDocument(name="t")
    table = doc.add_table(data=TableData(num_rows=4, num_cols=3, table_cells=cells))
    md = table_to_markdown(table, doc).split("\n")
    assert md[0] == "| Model | BLEU / EN-DE | BLEU / EN-FR |"
    assert md[1] == "|---|---|---|"
    assert md[2] == "| ByteNet | 23.75 |  |"
    assert md[3] == "| Transformer \\| big | 28.4 | 41.8 |"


def test_title_normalization_and_junk():
    assert norm_title("Chapter 2 A Tale of Two Values") == norm_title("A TALE OF TWO VALUES")
    assert norm_title("PART II Starting with the Bricks") == norm_title("II STARTING WITH THE BRICKS")
    for junk in ("Click here to view code image", "point.h", "Main.java"):
        assert JUNK_HEADING_RE.match(junk)
    assert not JUNK_HEADING_RE.match("Dependency Inversion")


def test_atomic_blocks_survive_parsing_and_chunking():
    code = "\n".join(f"    line_{i} = compute({i})  # step" for i in range(60))
    table = "| A | B |\n|---|---|\n" + "\n".join(f"| row {i} | value {i} |" for i in range(120))
    md = (
        "## 1 Introduction\n\nIntro text.\n\n"
        "```python\n# not a heading\n1. not a list\n\n" + code + "\n```\n\n"
        + table + "\n\n"
        "$$ \\frac{QK^T}{\\sqrt{d_k}} $$\n\n"
        "![Figure 1: Arch](figure://6f1c0b5e-0000-4000-8000-000000000001/figure_1.png)\n\n"
        "## 2 Background\n\nMore."
    )
    root = parse_markdown(md, "paper")
    assert [n.display() for n in root.children] == ["1. Introduction", "2. Background"]
    intro = root.children[0]
    assert "# not a heading" in intro.own_text and "    line_5 = compute(5)" in intro.own_text
    chunks = build_chunks(root, max_chars=1500, overlap=1)
    bodies = [c.content for c in chunks if c.node is intro]
    code_chunks = [b for b in bodies if "```python" in b]
    assert len(code_chunks) == 1 and code_chunks[0].count("```") == 2
    table_chunks = [b for b in bodies if "| row" in b]
    assert len(table_chunks) >= 2 and all("| A | B |\n|---|---|" in b for b in table_chunks)
    assert sum(b.count("| row ") for b in table_chunks) == 120
    assert any("$$ \\frac{QK^T}{\\sqrt{d_k}} $$" in b for b in bodies)
    emb = embedding_text("paper", intro, bodies[-1])
    assert "figure://" not in emb


def test_split_paragraphs_keeps_fence_with_blank_lines():
    text = "Para one.\n\n```\nalpha\n\nbeta\n```\n\nPara two."
    parts = split_paragraphs(text, 10_000, 0)
    assert parts == [text]
