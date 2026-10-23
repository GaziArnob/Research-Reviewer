import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

TEMPLATE_DIR = Path(__file__).resolve().parent.parent.parent / "templates"


class Provenance(BaseModel):
    level: str  # verified | partial
    checked: str
    official_urls: list[str] = Field(default_factory=list)
    notes: str = ""


class Policy(BaseModel):
    """What the venue's own reviewer page says about AI tools. 'not_stated' means the page was checked and is silent."""
    llm_review_text: str = "not_stated"  # forbidden | allowed_assist | not_stated
    third_party_services: str = "not_stated"  # discouraged | not_stated
    notes: list[str] = Field(default_factory=list)


class Section(BaseModel):
    key: str
    label: str
    type: str  # text | scale | choice | ack
    guidance: str = ""
    fill: Optional[str] = None  # which AI-prepared evidence goes here (see render.py)
    source: str = "official"  # official | secondary | unverified
    scale: Optional[dict] = None
    choices: list[str] = Field(default_factory=list)


class ChecklistItem(BaseModel):
    id: str
    text: str
    options: list[str] = Field(default_factory=list)
    group: str = ""
    auto: Optional[str] = None
    gate: Optional[str] = None
    requires: Optional[str] = None


class Template(BaseModel):
    id: str
    name: str
    provenance: Provenance
    policy: Policy = Field(default_factory=Policy)
    sections: list[Section]
    checklist: list[ChecklistItem] = Field(default_factory=list)
    criteria: list[str] = Field(default_factory=list)


@lru_cache(maxsize=None)
def load_template(template_id: str) -> Template:
    return Template(**json.loads((TEMPLATE_DIR / f"{template_id}.json").read_text(encoding="utf-8")))


def list_templates() -> list[Template]:
    return [load_template(p.stem) for p in sorted(TEMPLATE_DIR.glob("*.json"))]
