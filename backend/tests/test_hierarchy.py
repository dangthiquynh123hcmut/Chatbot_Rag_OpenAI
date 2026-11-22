from app.converters import normalize_markdown
from app.hierarchy import build_chunks, parse_markdown, tree_to_markdown
from app.references import parse_references

LEGAL = """
LUẬT AN TOÀN THÔNG TIN MẠNG

Căn cứ Hiến pháp nước Cộng hòa xã hội chủ nghĩa Việt Nam;

CHƯƠNG I
NHỮNG QUY ĐỊNH CHUNG

Điều 1. Phạm vi điều chỉnh
Luật này quy định về hoạt động an toàn thông tin mạng, quyền, trách nhiệm của cơ quan,
tổ chức, cá nhân trong việc bảo đảm an toàn thông tin mạng.

Điều 2. Đối tượng áp dụng
1. Cơ quan, tổ chức, cá nhân Việt Nam.
2. Tổ chức, cá nhân nước ngoài trực tiếp tham gia, theo quy định tại
Điều 5 của Luật này.

Chương II
BẢO ĐẢM AN TOÀN THÔNG TIN MẠNG

Mục 1
BẢO VỆ THÔNG TIN MẠNG

Điều 3. Nguyên tắc
1. Bảo đảm an toàn thông tin là trách nhiệm của mọi cơ quan.
2. Hoạt động an toàn thông tin phải thực hiện theo các yêu cầu sau:
a) Đúng quy định pháp luật;
b) Bảo đảm lợi ích quốc gia;
c) Không xâm phạm quyền riêng tư.
3. Các trường hợp khác theo quy định của Chính phủ.

Mục 2
PHÒNG, CHỐNG PHẦN MỀM ĐỘC HẠI

Điều 4. Trách nhiệm phòng chống
1. Doanh nghiệp phải có biện pháp phòng chống phần mềm độc hại.
2. Người dùng phải cập nhật phần mềm.
"""

TEXTBOOK = """
## Chương 1. KHÁI NIỆM VÀ ĐỐI TƯỢNG

I. KHÁI NIỆM TƯ TƯỞNG

1. Định nghĩa
Tư tưởng là hệ thống quan điểm.

1.1. Nguồn gốc
Hình thành từ thực tiễn.
Các bước:
1. Thu thập
2. Phân tích

1.2. Ý nghĩa
Có ý nghĩa to lớn.

2. Đối tượng nghiên cứu
Là hệ thống quan điểm toàn diện.

II. PHƯƠNG PHÁP

1. Phương pháp luận
C. Mác và Ph. Ăngghen đã chỉ ra.
"""


def _find(root, path_suffix):
    for n in root.iter():
        if n.kind != "document" and n.path[-1] == path_suffix:
            return n
    return None


def test_legal_tree():
    root = parse_markdown(normalize_markdown(LEGAL), "Luật ATTT")
    art2 = _find(root, "Điều 2. Đối tượng áp dụng")
    assert art2 is not None
    assert [c.label for c in art2.children] == ["Khoản 1", "Khoản 2"]
    assert "Điều 5 của Luật này" in art2.children[1].full_markdown()
    art3 = _find(root, "Điều 3. Nguyên tắc")
    assert art3.refs == {"chapter": "2", "section": "1", "article": "3"}
    k2 = art3.children[1]
    assert [c.label for c in k2.children] == ["Điểm a", "Điểm b", "Điểm c"]
    assert k2.children[1].refs == {"chapter": "2", "section": "1", "article": "3", "clause": "2", "point": "b"}
    assert art3.children[2].label == "Khoản 3"
    assert art3.path[:2] == ["Chương II. BẢO ĐẢM AN TOÀN THÔNG TIN MẠNG", "Mục 1. BẢO VỆ THÔNG TIN MẠNG"]
    assert "Căn cứ Hiến pháp" in root.own_text


def test_textbook_tree():
    root = parse_markdown(normalize_markdown(TEXTBOOK), "Giáo trình")
    ch = root.children[0]
    assert ch.kind == "chapter" and ch.number == "1"
    roman = [c.label for c in ch.children]
    assert roman == ["I", "II"]
    one = ch.children[0].children[0]
    assert one.label == "1" and [c.label for c in one.children] == ["1.1", "1.2"]
    assert "1. Thu thập" in one.children[0].own_text
    assert "C. Mác" in ch.children[1].children[0].own_text


def test_chunks_keep_sections():
    root = parse_markdown(normalize_markdown(LEGAL), "Luật ATTT")
    chunks = build_chunks(root, max_chars=400, overlap=1)
    kinds = {c.node.kind for c in chunks}
    assert "article" in kinds
    for c in chunks:
        if c.node.kind != "document":
            assert c.content.lstrip("# ").startswith(c.node.heading.split(" ")[0])
    md = tree_to_markdown(root)
    assert "### Điều 3. Nguyên tắc" in md or "#### Điều 3. Nguyên tắc" in md


def test_long_section_split():
    body = "\n\n".join(f"Đoạn {i} " + "nội dung " * 40 for i in range(10))
    root = parse_markdown(f"Điều 1. Dài\n{body}", "X")
    chunks = build_chunks(root, max_chars=800, overlap=1)
    assert len(chunks) > 1
    assert all(c.node.number == "1" for c in chunks)
    assert all(c.content.startswith("# Điều 1. Dài") for c in chunks)
    assert all(len(c.content) <= 800 for c in chunks)


def test_parse_references():
    refs = parse_references("Điểm b khoản 2 Điều 3 quy định gì?")
    assert refs == {"article": "3", "clause": "2", "point": "b"}
    assert parse_references("Nội dung chương II mục 1") == {"chapter": "2", "section": "1"}
    assert parse_references("mục 1.2 nói gì") == {"num": "1.2"}
    assert parse_references("an toàn thông tin là gì") == {}
