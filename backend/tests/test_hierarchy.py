from app.converters import normalize_markdown
from app.hierarchy import build_chunks, parse_markdown, tree_to_markdown
from app.references import parse_references

LEGAL = """
NETWORK INFORMATION SECURITY ACT

Pursuant to the Constitution;

CHAPTER I
GENERAL PROVISIONS

Article 1. Scope
This Act governs network information security activities and the rights and duties of agencies,
organizations and individuals in ensuring network information security.

Article 2. Subjects of application
1. Domestic agencies, organizations and individuals.
2. Foreign organizations and individuals directly involved, as provided in
Article 5 of this Act.

Chapter II
ENSURING NETWORK INFORMATION SECURITY

Section 1
PROTECTING NETWORK INFORMATION

Article 3. Principles
1. Ensuring information security is the duty of every agency.
2. Information security activities must meet the following requirements:
a) Comply with the law;
b) Protect national interests;
c) Not infringe on privacy.
3. Other cases as prescribed by the Government.

Section 2
PREVENTING MALWARE

Article 4. Responsibility for prevention
1. Enterprises must have measures to prevent malware.
2. Users must keep their software updated.
"""

TEXTBOOK = """
## Chapter 1. CONCEPTS AND SUBJECT

I. THE CONCEPT OF THOUGHT

1. Definition
Thought is a system of views.

1.1. Origins
Formed from practice.
Steps:
1. Collect
2. Analyze

1.2. Significance
It is highly significant.

2. Subject of study
A comprehensive system of views.

II. METHODS

1. Methodology
C. Marx and F. Engels pointed out.
"""


def _find(root, path_suffix):
    for n in root.iter():
        if n.kind != "document" and n.path[-1] == path_suffix:
            return n
    return None


def test_legal_tree():
    root = parse_markdown(normalize_markdown(LEGAL), "Network Security Act")
    art2 = _find(root, "Article 2. Subjects of application")
    assert art2 is not None
    assert [c.label for c in art2.children] == ["Paragraph 1", "Paragraph 2"]
    assert "Article 5 of this Act" in art2.children[1].full_markdown()
    art3 = _find(root, "Article 3. Principles")
    assert art3.refs == {"chapter": "2", "section": "1", "article": "3"}
    k2 = art3.children[1]
    assert [c.label for c in k2.children] == ["Point a", "Point b", "Point c"]
    assert k2.children[1].refs == {"chapter": "2", "section": "1", "article": "3", "clause": "2", "point": "b"}
    assert art3.children[2].label == "Paragraph 3"
    assert art3.path[:2] == ["Chapter II. ENSURING NETWORK INFORMATION SECURITY", "Section 1. PROTECTING NETWORK INFORMATION"]
    assert "Pursuant to the Constitution" in root.own_text


def test_textbook_tree():
    root = parse_markdown(normalize_markdown(TEXTBOOK), "Textbook")
    ch = root.children[0]
    assert ch.kind == "chapter" and ch.number == "1"
    roman = [c.label for c in ch.children]
    assert roman == ["I", "II"]
    one = ch.children[0].children[0]
    assert one.label == "1" and [c.label for c in one.children] == ["1.1", "1.2"]
    assert "1. Collect" in one.children[0].own_text
    assert "C. Marx" in ch.children[1].children[0].own_text


def test_chunks_keep_sections():
    root = parse_markdown(normalize_markdown(LEGAL), "Network Security Act")
    chunks = build_chunks(root, max_chars=400, overlap=1)
    kinds = {c.node.kind for c in chunks}
    assert "article" in kinds
    for c in chunks:
        if c.node.kind != "document":
            assert c.content.lstrip("# ").startswith(c.node.heading.split(" ")[0])
    md = tree_to_markdown(root)
    assert "### Article 3. Principles" in md or "#### Article 3. Principles" in md


def test_long_section_split():
    body = "\n\n".join(f"Paragraph {i} " + "content " * 40 for i in range(10))
    root = parse_markdown(f"Article 1. Long\n{body}", "X")
    chunks = build_chunks(root, max_chars=800, overlap=1)
    assert len(chunks) > 1
    assert all(c.node.number == "1" for c in chunks)
    assert all(c.content.startswith("# Article 1. Long") for c in chunks)
    assert all(len(c.content) <= 800 for c in chunks)


def test_parse_references():
    refs = parse_references("What does point b of paragraph 2 of Article 3 provide?")
    assert refs == {"article": "3", "clause": "2", "point": "b"}
    assert parse_references("Content of chapter II section 1") == {"chapter": "2", "section": "1"}
    assert parse_references("what does section 1.2 say") == {"num": "1.2"}
    assert parse_references("what is information security") == {}


def test_vietnamese_keywords_not_structural():
    root = parse_markdown("Chương I\nĐiều 1. Phạm vi\n1. Một\n2. Hai", "x")
    assert all(n.kind not in ("chapter", "article") for n in root.iter())
    assert parse_references("Khoản 2 Điều 3") == {}
