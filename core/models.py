import re
import uuid
from typing import Literal, Optional

from pydantic import BaseModel, Field

Status = Literal["verified", "needs_verification", "uncertain"]
Kind = Literal["major", "minor", "question", "flag", "strength"]


class Section(BaseModel):
    name: str
    page_start: int
    page_end: int
    text: str


class Ref(BaseModel):
    key: str
    raw: str
    doi: Optional[str] = None
    title: Optional[str] = None
    year: Optional[int] = None


class HiddenSpan(BaseModel):
    page: int
    text: str
    reason: str


class ParsedPaper(BaseModel):
    sha256: str
    filename: str
    title: str = ""
    pages: list[str] = Field(default_factory=list)
    sections: list[Section] = Field(default_factory=list)
    references: list[Ref] = Field(default_factory=list)
    hidden_spans: list[HiddenSpan] = Field(default_factory=list)
    figure_numbers: list[int] = Field(default_factory=list)
    table_numbers: list[int] = Field(default_factory=list)
    image_count: int = 0

    @property
    def full_text(self) -> str:
        return "\n".join(self.pages)

    @property
    def word_count(self) -> int:
        return len(re.findall(r"\w+", self.full_text))

    def section(self, *names: str) -> Optional[Section]:
        wanted = {n.lower() for n in names}
        for s in self.sections:
            if s.name.lower() in wanted:
                return s
        return None

    @property
    def body_text(self) -> str:
        """Full text without the references section."""
        return "\n".join(s.text for s in self.sections if s.name.lower() != "references")


class Evidence(BaseModel):
    quote: str
    section: Optional[str] = None
    page: Optional[int] = None
    source: str = "paper"


class Finding(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    module: str
    kind: Kind
    text: str
    evidence: list[Evidence] = Field(default_factory=list)
    status: Status = "needs_verification"
    confidence: float = 1.0
    source: str = "rule"


class RelatedPaper(BaseModel):
    title: str
    year: Optional[int] = None
    venue: Optional[str] = None
    url: Optional[str] = None
    abstract: Optional[str] = None
    source: str


class Claim(BaseModel):
    text: str
    quote: str
    section: Optional[str] = None
    page: Optional[int] = None


class Scores(BaseModel):
    technical: int
    novelty: int
    methodology: int
    clarity: int
    reproducibility: int
    overall: float
    recommendation: str
    confidence: float
    justification: str
    calibrated: bool = False
    raw_overall: Optional[float] = None
    adjustment_note: str = ""


class PipelineResult(BaseModel):
    sha256: str
    filename: str
    title: str = ""
    status: Literal["ok", "rejected"] = "ok"
    findings: list[Finding] = Field(default_factory=list)
    related: list[RelatedPaper] = Field(default_factory=list)
    trends: dict = Field(default_factory=dict)
    completeness: dict = Field(default_factory=dict)
    understanding: Optional[dict] = None
    claims: list[Claim] = Field(default_factory=list)
    scores: Optional[Scores] = None
    llm_model: Optional[str] = None
    step_log: list[str] = Field(default_factory=list)
    stats: dict = Field(default_factory=dict)
