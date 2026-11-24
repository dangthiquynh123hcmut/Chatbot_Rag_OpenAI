from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass, field

ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
LETTERS = "abcdefghijklmnopqrstuvwxyz"

SEP = r"[\s.:\-–—]*"
NUM_TOKEN = r"([IVXLCDM]+|\d+)"

STRUCT_PATTERNS = [
    ("part", 10, re.compile(rf"^part\s+{NUM_TOKEN}(?![\w])({SEP})(.*)$", re.IGNORECASE)),
    ("chapter", 20, re.compile(rf"^chapter\s+{NUM_TOKEN}(?![\w])({SEP})(.*)$", re.IGNORECASE)),
    ("subsection", 40, re.compile(rf"^subsection\s+{NUM_TOKEN}(?![\w])({SEP})(.*)$", re.IGNORECASE)),
    ("section", 30, re.compile(rf"^section\s+{NUM_TOKEN}(?![\w])({SEP})(.*)$", re.IGNORECASE)),
]
ARTICLE_RE = re.compile(r"^article\s+(\d+[a-z]?)\s*([.:])?\s*(.*)$", re.IGNORECASE)
ROMAN_RE = re.compile(r"^([IVXLCDM]+)\s*[.)]\s+(\S.*)$")
DECIMAL_RE = re.compile(r"^(\d{1,3}(?:\.\d{1,3})+)\.?\s+(\S.*)$")
NUMBER_RE = re.compile(r"^(\d{1,3})\s*[.)]\s+(\S.*)$")
MARKED_NUMBER_RE = re.compile(r"^(\d{1,3})\s+([^\d\s].{0,200})$")
POINT_RE = re.compile(r"^([a-z])\s*\)\s+(\S.*)$")
MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
ATOMIC_LINE_PREFIXES = ("|", "$$", "![")
FIGURE_MD_RE = re.compile(r"!\[([^\]]*)\]\(figure://[^)]+\)")

KIND_LABELS = {
    "part": "Part",
    "chapter": "Chapter",
    "section": "Section",
    "subsection": "Subsection",
    "article": "Article",
    "clause": "Paragraph",
    "point": "Point",
}
INLINE_KINDS = {"clause", "point"}
MD_RANKS = {1: 15, 2: 25, 3: 35, 4: 47, 5: 57, 6: 67}
PREAMBLE = "Preamble"


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def roman_to_int(s: str) -> int | None:
    s = s.upper()
    if not s or any(c not in ROMAN_VALUES for c in s):
        return None
    total, prev = 0, 0
    for c in reversed(s):
        v = ROMAN_VALUES[c]
        total += -v if v < prev else v
        prev = max(prev, v)
    return total


def normalize_number(raw: str) -> str:
    raw = raw.strip().strip(".:)").strip()
    low = raw.lower()
    if raw.isdigit():
        return str(int(raw))
    if re.fullmatch(r"\d+[a-z]", low):
        return str(int(low[:-1])) + low[-1]
    if re.fullmatch(r"[IVXLCDM]+", raw.upper()) and len(raw) > 0 and raw.upper() == raw:
        v = roman_to_int(raw)
        if v:
            return str(v)
    if re.fullmatch(r"\d+(\.\d+)+", raw):
        return ".".join(str(int(p)) for p in raw.split("."))
    return low


def number_value(num: str) -> float:
    m = re.match(r"(\d+)([a-z]?)", num)
    if not m:
        return -1
    return int(m.group(1)) + (0.5 if m.group(2) else 0)


def clean_line(line: str) -> tuple[str, bool]:
    s = line.strip()
    marked = False
    m = MD_HEADING_RE.match(s)
    if m:
        s = m.group(2).strip()
        marked = True
    if s.startswith("**") or s.startswith("__"):
        marked = True
    s = s.replace("**", "").replace("__", "")
    s = re.sub(r"^[*_]+|[*_]+$", "", s).strip()
    s = re.sub(r"\s+", " ", s)
    return s, marked


@dataclass
class Candidate:
    kind: str
    rank: int
    raw_number: str
    number: str
    title: str
    heading: str
    depth_hint: int = 0
    marked: bool = False
    label_word: str = ""


@dataclass
class Node:
    kind: str
    rank: int
    number: str = ""
    label: str = ""
    title: str = ""
    heading: str = ""
    lines: list[str] = field(default_factory=list)
    children: list["Node"] = field(default_factory=list)
    parent: "Node | None" = None
    order: int = 0
    depth: int = 0
    md_level: int | None = None

    @property
    def own_text(self) -> str:
        return "\n".join(self.lines).strip()

    def ancestors(self) -> list["Node"]:
        out, cur = [], self.parent
        while cur is not None and cur.kind != "document":
            out.append(cur)
            cur = cur.parent
        return list(reversed(out))

    def display(self) -> str:
        if self.kind == "document":
            return self.title or "Document"
        if self.kind in INLINE_KINDS or not self.title:
            return self.label
        short = self.title if len(self.title) <= 90 else self.title[:87].rstrip() + "…"
        if self.kind == "md":
            return short
        return f"{self.label}. {short}"

    @property
    def path(self) -> list[str]:
        return [a.display() for a in self.ancestors()] + [self.display()]

    @property
    def refs(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for n in self.ancestors() + [self]:
            if n.kind in ("md", "document") or not n.number:
                continue
            out[n.kind] = n.number
        return out

    def full_markdown(self, base_depth: int | None = None) -> str:
        base = self.depth if base_depth is None else base_depth
        parts = []
        if self.kind != "document":
            parts.append(render_heading(self, self.depth - base + 1))
        if self.own_text:
            parts.append(self.own_text)
        for c in self.children:
            parts.append(c.full_markdown(base))
        return "\n\n".join(p for p in parts if p).strip()

    def own_markdown(self) -> str:
        parts = []
        if self.kind != "document":
            parts.append(render_heading(self, 1))
        if self.own_text:
            parts.append(self.own_text)
        return "\n\n".join(parts).strip()

    def iter(self) -> Iterator["Node"]:
        yield self
        for c in self.children:
            yield from c.iter()


def render_heading(node: Node, level: int) -> str:
    if node.kind in INLINE_KINDS:
        return node.heading
    return "#" * max(1, min(level, 6)) + " " + node.heading


def match_structural(text: str, marked: bool) -> Candidate | None:
    cand = _match_structural(text, marked)
    if cand is not None:
        cand.marked = marked
    return cand


def _match_structural(text: str, marked: bool) -> Candidate | None:
    if not text or text.startswith("|") or len(text) > 400:
        return None
    for kind, rank, rx in STRUCT_PATTERNS:
        m = rx.match(text)
        if not m:
            continue
        raw, sep, rest = m.group(1), m.group(2), m.group(3).strip()
        if not (raw.isdigit() or raw.upper() == raw):
            continue
        sep_has_punct = bool(re.search(r"[.:\-–—]", sep))
        if rest and not (sep_has_punct or marked or rest.upper() == rest):
            continue
        if len(rest) > 250:
            continue
        label = KIND_LABELS[kind]
        heading = f"{label} {raw}" + (f". {rest}" if rest else "")
        return Candidate(kind, rank, raw, normalize_number(raw), rest, heading, label_word=label)
    m = ARTICLE_RE.match(text)
    if m:
        raw, punct, rest = m.group(1), m.group(2), m.group(3).strip()
        if punct or marked or not rest:
            label = KIND_LABELS["article"]
            heading = f"{label} {raw}." + (f" {rest}" if rest else "")
            return Candidate("article", 50, raw, normalize_number(raw), rest, heading, label_word=label)
        return None
    m = ROMAN_RE.match(text)
    if m:
        raw, rest = m.group(1), m.group(2).strip()
        if (len(raw) > 1 or raw in "IVX") and roman_to_int(raw):
            return Candidate("roman", 45, raw, normalize_number(raw), rest, f"{raw}. {rest}")
    m = DECIMAL_RE.match(text)
    if m:
        raw, rest = m.group(1), m.group(2).strip()
        depth = raw.count(".") + 1
        return Candidate("num", 60 + 2 * (depth - 1), raw, normalize_number(raw), rest, f"{raw}. {rest}", depth)
    m = NUMBER_RE.match(text) or (MARKED_NUMBER_RE.match(text) if marked else None)
    if m:
        raw, rest = m.group(1), m.group(2).strip()
        return Candidate("num", 60, raw, normalize_number(raw), rest, f"{raw}. {rest}", 1)
    m = POINT_RE.match(text)
    if m:
        raw, rest = m.group(1), m.group(2).strip()
        return Candidate("point", 75, raw, raw.lower(), rest, f"{raw}) {rest}")
    return None


def is_successor(kind: str, prev: str, cur: str) -> bool:
    if kind == "point":
        i = LETTERS.find(prev)
        return 0 <= i < len(LETTERS) - 1 and LETTERS[i + 1] == cur
    try:
        return int(cur.split(".")[-1]) == int(prev.split(".")[-1]) + 1
    except ValueError:
        return False


def classify_line(line: str) -> Candidate | None:
    text, marked = clean_line(line)
    return match_structural(text, marked)


class HierarchyParser:
    def __init__(self, title: str = ""):
        self.root = Node(kind="document", rank=0, title=title)
        self.stack: list[Node] = [self.root]
        self.last_article = -1.0
        self.order = 0
        self.body_list: tuple[Node, str, int, str] | None = None

    def _parent_for(self, rank: int, kind: str = "md", md_level: int | None = None) -> Node:
        for n in reversed(self.stack):
            if n is self.root:
                return n
            if kind == "md":
                if md_level is not None and n.md_level is not None and n.md_level >= md_level:
                    continue
                if n.md_level is None and n.kind in ("num", "roman"):
                    continue
                if n.kind != "md" or n.rank < rank:
                    return n
                continue
            if n.kind == "md":
                continue
            if n.rank < rank:
                return n
        return self.root

    @staticmethod
    def _last_child(parent: Node, kind: str, rank: int | None = None) -> Node | None:
        kinds = {"num", "clause"} if kind == "num" else {kind}
        for c in reversed(parent.children):
            if c.kind in kinds and (rank is None or c.rank == rank):
                return c
        return None

    @staticmethod
    def _article_ancestor(node: Node) -> Node | None:
        cur: Node | None = node
        while cur is not None:
            if cur.kind == "article":
                return cur
            cur = cur.parent
        return None

    def _is_inside_article(self, parent: Node) -> bool:
        cur: Node | None = parent
        while cur is not None:
            if cur.kind == "article":
                return True
            cur = cur.parent
        return False

    def _accept(self, cand: Candidate, parent: Node) -> bool:
        if cand.kind == "article":
            v = number_value(cand.number)
            return v > self.last_article
        prev = self._last_child(parent, cand.kind, cand.rank if cand.kind == "num" else None)
        if cand.kind in ("part", "chapter", "section", "subsection"):
            if prev is None:
                return True
            return number_value(cand.number) > number_value(prev.number)
        if cand.kind == "roman":
            cur = int(cand.number)
            return (prev is None and cur == 1) or (prev is not None and cur == int(prev.number) + 1)
        if cand.kind == "num":
            if not cand.marked and not self._is_inside_article(parent) and (
                len(cand.title) > 120 or cand.title.rstrip().endswith((".", ";", ","))
            ):
                return False
            parts = cand.number.split(".")
            last = int(parts[-1])
            if len(parts) > 1:
                prefix = ".".join(parts[:-1])
                if parent.kind in ("num", "clause") and parent.number != prefix:
                    return False
            if prev is None:
                return last == 1
            return last == int(prev.number.split(".")[-1]) + 1
        if cand.kind == "point":
            if prev is None:
                return cand.number == "a"
            return is_successor("point", prev.number, cand.number)
        return True

    def _push(self, cand: Candidate, parent: Node, md_level: int | None = None) -> Node:
        while self.stack and self.stack[-1] is not parent:
            self.stack.pop()
        kind = cand.kind
        label = cand.label_word or KIND_LABELS.get(kind, "")
        article = self._article_ancestor(parent)
        if kind == "num":
            if cand.depth_hint == 1 and article is not None:
                kind, label = "clause", KIND_LABELS["clause"] + f" {cand.number}"
            else:
                label = cand.raw_number.rstrip(".")
        elif kind == "point":
            label = (KIND_LABELS["point"] + f" {cand.number}") if article is not None else f"{cand.number})"
        elif kind == "roman":
            label = cand.raw_number
        elif kind == "md":
            label = cand.title
        else:
            label = f"{label} {cand.raw_number}"
        self.order += 1
        node = Node(
            kind=kind,
            rank=cand.rank,
            number=cand.number,
            label=label,
            title=cand.title if kind not in INLINE_KINDS else "",
            heading=cand.heading,
            parent=parent,
            order=self.order,
            depth=parent.depth + 1,
            md_level=md_level,
        )
        if kind in INLINE_KINDS:
            node.title = ""
        parent.children.append(node)
        self.stack.append(node)
        if kind == "article":
            self.last_article = number_value(cand.number)
        return node

    def feed(self, lines: list[str]) -> Node:
        i, n = 0, len(lines)
        in_fence = False
        while i < n:
            raw_line = lines[i]
            if in_fence or FENCE_RE.match(raw_line):
                if FENCE_RE.match(raw_line):
                    in_fence = not in_fence
                self.stack[-1].lines.append(raw_line.rstrip())
                i += 1
                continue
            if raw_line.lstrip().startswith(ATOMIC_LINE_PREFIXES):
                self.stack[-1].lines.append(raw_line.strip())
                i += 1
                continue
            text, marked = clean_line(raw_line)
            if not text:
                self.stack[-1].lines.append("")
                i += 1
                continue
            cand = match_structural(text, marked)
            hm = MD_HEADING_RE.match(raw_line.strip())
            md_level = len(hm.group(1)) if hm else None
            if cand is None and marked and MD_HEADING_RE.match(raw_line.strip()):
                level = len(MD_HEADING_RE.match(raw_line.strip()).group(1))
                if level == 1 and not self.root.children:
                    self.root.lines.append(f"**{text}**")
                    i += 1
                    continue
                cand = Candidate("md", MD_RANKS[level], "", "", text, text)
            if cand is not None and cand.kind in ("num", "point"):
                bl = self.body_list
                if bl and bl[0] is self.stack[-1] and bl[1] == cand.kind and bl[2] == cand.rank and is_successor(
                    cand.kind, bl[3], cand.number
                ):
                    self.body_list = (bl[0], bl[1], bl[2], cand.number)
                    cand = None
            if cand is not None:
                parent = self._parent_for(cand.rank, cand.kind, md_level)
                accepted = cand.kind == "md" or self._accept(cand, parent)
                if not accepted and cand.kind in ("num", "point"):
                    self.body_list = (self.stack[-1], cand.kind, cand.rank, cand.number)
                if accepted:
                    self.body_list = None
                    if cand.kind in ("part", "chapter", "section", "subsection", "article") and not cand.title:
                        j = i + 1
                        while j < n and not lines[j].strip():
                            j += 1
                        if j < n:
                            nxt, _ = clean_line(lines[j])
                            if nxt and len(nxt) <= 200 and classify_line(lines[j]) is None and (
                                nxt.upper() == nxt or cand.kind == "article"
                            ):
                                cand.title = nxt
                                cand.heading = f"{cand.heading.rstrip('.')}. {nxt}" if cand.kind != "article" else f"{cand.heading} {nxt}"
                                i = j
                    self._push(cand, parent, md_level)
                    i += 1
                    continue
            self.stack[-1].lines.append(text if not raw_line.lstrip().startswith("|") else raw_line.strip())
            i += 1
        self._tidy(self.root)
        return self.root

    def _tidy(self, node: Node) -> None:
        cleaned, blank, in_fence = [], False, False
        for ln in node.lines:
            if FENCE_RE.match(ln):
                in_fence = not in_fence
            if in_fence and not FENCE_RE.match(ln):
                cleaned.append(ln)
                blank = False
                continue
            if not ln.strip():
                if cleaned and not blank:
                    cleaned.append("")
                blank = True
            else:
                cleaned.append(ln)
                blank = False
        while cleaned and not cleaned[-1].strip():
            cleaned.pop()
        node.lines = cleaned
        for c in node.children:
            self._tidy(c)


def parse_markdown(markdown: str, title: str = "") -> Node:
    return HierarchyParser(nfc(title)).feed(nfc(markdown).splitlines())


def tree_to_markdown(root: Node) -> str:
    parts = []
    if root.title:
        parts.append(f"# {root.title}")
    if root.own_text:
        parts.append(root.own_text)
    for c in root.children:
        parts.append(c.full_markdown(base_depth=0 if root.title else 1))
    return "\n\n".join(p for p in parts if p).strip() + "\n"


@dataclass
class Chunk:
    node: Node
    part: int
    content: str


def atomic_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    in_fence = False
    for line in text.split("\n"):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            current.append(line)
            continue
        if not in_fence and not line.strip():
            if current:
                blocks.append("\n".join(current).strip())
                current = []
            continue
        current.append(line)
    if current:
        blocks.append("\n".join(current).strip())
    return [b for b in blocks if b]


def is_table_block(block: str) -> bool:
    lines = block.split("\n")
    return len(lines) >= 2 and all(ln.lstrip().startswith("|") for ln in lines)


def split_table(block: str, max_chars: int) -> list[str]:
    lines = block.split("\n")
    header, body = lines[:2], lines[2:]
    head_len = sum(len(h) + 1 for h in header)
    parts, cur, size = [], [], head_len
    for row in body:
        if cur and size + len(row) + 1 > max_chars:
            parts.append("\n".join(header + cur))
            cur, size = [], head_len
        cur.append(row)
        size += len(row) + 1
    if cur or not parts:
        parts.append("\n".join(header + cur))
    return parts


def split_paragraphs(text: str, max_chars: int, overlap: int) -> list[str]:
    paras = atomic_blocks(text)
    units: list[str] = []
    for p in paras:
        if len(p) <= max_chars:
            units.append(p)
            continue
        if is_table_block(p):
            units.extend(split_table(p, max_chars))
            continue
        if FENCE_RE.match(p) or p.startswith("$$") or p.startswith("!["):
            units.append(p)
            continue
        sentences = re.split(r"(?<=[.!?;…])\s+", p)
        buf = ""
        for s in sentences:
            if buf and len(buf) + len(s) + 1 > max_chars:
                units.append(buf)
                buf = s
            else:
                buf = f"{buf} {s}".strip()
            while len(buf) > max_chars:
                units.append(buf[:max_chars])
                buf = buf[max_chars:]
        if buf:
            units.append(buf)
    out: list[str] = []
    cur: list[str] = []
    size = 0
    for u in units:
        if cur and size + len(u) + 2 > max_chars:
            out.append("\n\n".join(cur))
            keep = cur[-overlap:] if overlap > 0 else []
            if sum(len(k) for k in keep) + len(u) > max_chars:
                keep = []
            cur, size = list(keep), sum(len(k) + 2 for k in keep)
        cur.append(u)
        size += len(u) + 2
    if cur:
        out.append("\n\n".join(cur))
    return out


def build_chunks(root: Node, max_chars: int = 2000, overlap: int = 1) -> list[Chunk]:
    chunks: list[Chunk] = []

    def emit_text(node: Node, text: str) -> None:
        if not text.strip():
            return
        if len(text) <= max_chars:
            chunks.append(Chunk(node, 0, text))
            return
        heading = render_heading(node, 1) if node.kind != "document" else ""
        body = text[len(heading):].strip() if heading and text.startswith(heading) else text
        budget = max(300, max_chars - len(heading) - 2)
        for idx, piece in enumerate(split_paragraphs(body, budget, overlap)):
            content = f"{heading}\n\n{piece}".strip() if heading else piece
            chunks.append(Chunk(node, idx, content))

    def visit(node: Node) -> None:
        if node.kind == "document":
            if node.own_text:
                emit_text(node, node.own_text)
            for c in node.children:
                visit(c)
            return
        full = node.full_markdown()
        only_inline_children = all(c.kind in INLINE_KINDS for c in node.children)
        if not node.children or (only_inline_children and len(full) <= max_chars):
            emit_text(node, full)
            return
        if node.own_text:
            emit_text(node, node.own_markdown())
        for c in node.children:
            visit(c)

    visit(root)
    return chunks


def embedding_text(doc_title: str, node: Node, content: str) -> str:
    path = " > ".join(node.path) if node.kind != "document" else PREAMBLE
    content = FIGURE_MD_RE.sub(lambda m: f"[Figure: {m.group(1)}]", content)
    return f"Document: {doc_title}\nLocation: {path}\n\n{content}"
