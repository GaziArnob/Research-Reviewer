import re
from typing import Optional

from pydantic import BaseModel, Field

from core.assistant.rag import Chunk, PaperIndex
from core.llm.base import LLMClient
from core.llm.jsonutil import ask_json
from core.models import ParsedPaper
from core.prompts import load

MAX_SEARCHES = 3  # hard step limit: small models loop, and every step costs time
NOT_STATED = "The paper does not state this in the passages found."


class Citation(BaseModel):
    chunk_id: int
    section: str
    page: Optional[int] = None
    text: str


class QAResult(BaseModel):
    question: str
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    mode: str = "agentic"  # agentic | plain | failed
    steps: list[dict] = Field(default_factory=list)  # audit trail of tool calls
    warnings: list[str] = Field(default_factory=list)


def _passages(chunks: list[Chunk]) -> str:
    return "\n".join(f"[P{c.id}] ({c.section}, p.{c.page}) {c.text}" for c in chunks) or "(none yet)"


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+(?:\.\d+)?", text))


def _finish(question: str, data: dict, seen: dict[int, Chunk], index: PaperIndex, mode: str,
            steps: list[dict]) -> QAResult:
    answer = str(data.get("answer", "")).strip() or NOT_STATED
    raw = data.get("cited") or []
    raw = raw if isinstance(raw, list) else [raw]
    ids = [int(d) for d in (re.sub(r"\D", "", str(i)) for i in raw) if d]  # accepts 12, "12" and "P12"
    cited = [index.get(i) for i in ids]
    cited = [c for c in cited if c is not None]
    warnings = []
    if len(cited) != len(ids):
        warnings.append("The model cited passages that do not exist; those citations were removed.")
    if not cited and answer != NOT_STATED and not answer.lower().startswith("the paper does not"):
        warnings.append("The answer cites no passage from the paper. Treat it as unsupported.")
    cited_text = " ".join(c.text for c in cited)
    missing = sorted(n for n in _numbers(answer) - _numbers(cited_text) if len(n) > 1)
    if cited and missing:
        warnings.append(f"Numbers in the answer that do not appear in the cited passages: {', '.join(missing)}.")
    return QAResult(question=question, answer=answer, mode=mode, steps=steps, warnings=warnings,
                    citations=[Citation(chunk_id=c.id, section=c.section, page=c.page, text=c.text) for c in cited])


def answer_question(paper: ParsedPaper, question: str, llm: LLMClient,
                    index: Optional[PaperIndex] = None) -> QAResult:
    """Agentic RAG over one paper. The model may search up to MAX_SEARCHES times; if it fails to follow the
    JSON protocol the function falls back to plain retrieval + a single answer call."""
    index = index or PaperIndex(paper)
    system = load("qa")
    seen: dict[int, Chunk] = {c.id: c for c in index.retrieve(question, 4)}
    steps: list[dict] = [{"tool": "search_paper", "query": question, "found": sorted(seen)}]
    searches = 0

    for _ in range(MAX_SEARCHES + 1):
        left = MAX_SEARCHES - searches
        note = (f"Searches left: {left}." if left else "No searches left: you must answer now.")
        data = ask_json(llm, system, f"Question: {question}\n\nPassages so far:\n{_passages(list(seen.values()))}\n\n{note}")
        if not data:
            break
        action = str(data.get("action", "")).lower()
        if action == "search" and left:
            query = str(data.get("query", "")).strip() or question
            hits = index.retrieve(query, 4)
            seen.update({c.id: c for c in hits})
            steps.append({"tool": "search_paper", "query": query, "found": [c.id for c in hits]})
            searches += 1
            continue
        if action == "answer" or "answer" in data:
            return _finish(question, data, seen, index, "agentic", steps)
        break

    # Fallback: plain RAG, one forced answer.
    top = index.retrieve(question, 5)
    data = ask_json(llm, system, f"Question: {question}\n\nPassages:\n{_passages(top)}\n\n"
                                 "No searches left: you must answer now.")
    if data and ("answer" in data):
        steps.append({"tool": "fallback_plain_rag", "found": [c.id for c in top]})
        return _finish(question, data, {c.id: c for c in top}, index, "plain", steps)
    return QAResult(question=question, answer="The model did not return a usable answer. The passages below are the "
                    "closest matches in the paper.", mode="failed", steps=steps,
                    citations=[Citation(chunk_id=c.id, section=c.section, page=c.page, text=c.text) for c in top],
                    warnings=["No answer was generated."])
