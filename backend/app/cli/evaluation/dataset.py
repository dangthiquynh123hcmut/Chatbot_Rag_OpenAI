from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class EvalItem:
    id: str
    document: str
    question: str
    type: str
    answerable: bool = True
    gold_path: list[str] = field(default_factory=list)
    gold_refs: dict[str, str] = field(default_factory=dict)
    gold_quote: str = ""
    reference_answer: str = ""
    section_kind: str = ""
    reviewed: bool = False
    notes: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "EvalItem":
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        return cls(**known)


def load_dataset(path: str | Path) -> list[EvalItem]:
    items = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            items.append(EvalItem.from_dict(json.loads(line)))
    return items


def save_dataset(items: list[EvalItem], path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(asdict(i), ensure_ascii=False) + "\n" for i in items), encoding="utf-8")


def save_jsonl(rows: list[dict], path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in rows), encoding="utf-8")
