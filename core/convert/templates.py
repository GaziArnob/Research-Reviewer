import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

DIR = Path(__file__).resolve().parent.parent.parent / "paper_templates"


class Fact(BaseModel):
    text: str
    status: str  # official | unverified


class Provenance(BaseModel):
    level: str
    checked: str
    official_urls: list[str] = Field(default_factory=list)
    notes: str = ""


class LatexSpec(BaseModel):
    documentclass: str
    options: list[str] = Field(default_factory=list)
    preamble: list[str] = Field(default_factory=list)
    author_style: str
    keywords_style: str = "none"
    class_file: str = ""
    download: str = ""


class Layout(BaseModel):
    paper: str
    columns: int
    font: str
    font_pt: int
    margins_cm: Optional[float] = None
    layout_status: str = "unverified"


class Limits(BaseModel):
    main_pages: Optional[int] = None
    text: str = ""


class Anonymity(BaseModel):
    double_blind: bool
    text: str = ""


class PaperTemplate(BaseModel):
    id: str
    name: str
    kind: str  # conference | journal
    anonymous_default: bool
    provenance: Provenance
    latex: LatexSpec
    layout: Layout
    limits: Limits
    anonymity: Anonymity
    facts: list[Fact] = Field(default_factory=list)
    compile_check: Optional[dict] = None


@lru_cache(maxsize=None)
def load_paper_template(template_id: str) -> PaperTemplate:
    return PaperTemplate(**json.loads((DIR / f"{template_id}.json").read_text(encoding="utf-8")))


def list_paper_templates() -> list[PaperTemplate]:
    order = {"conference": 0, "journal": 1}
    return sorted((load_paper_template(p.stem) for p in DIR.glob("*.json")), key=lambda t: (order.get(t.kind, 2), t.name))
