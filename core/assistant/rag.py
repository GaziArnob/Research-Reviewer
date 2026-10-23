import re
from dataclasses import dataclass
from typing import Optional

from core.evidence.verifier import _norm
from core.models import ParsedPaper
from core.retrieval.bm25 import BM25

_SENT = re.compile(r"(?<=[.!?])\s+")


@dataclass
class Chunk:
    id: int
    section: str
    page: Optional[int]
    text: str


def _page_of(paper: ParsedPaper, text: str, fallback: int) -> int:
    probe = _norm(text[:80])
    if len(probe) >= 20:
        for page_no, page in enumerate(paper.pages, start=1):
            if probe in _norm(page):
                return page_no
    return fallback


def chunk_paper(paper: ParsedPaper, size: int = 900) -> list[Chunk]:
    """Sentence-aware chunks of about `size` characters, one run per section (references are left out)."""
    chunks: list[Chunk] = []
    for sec in paper.sections:
        if sec.name.lower() == "references":
            continue
        buf = ""
        for sent in _SENT.split(" ".join(sec.text.split())):
            if buf and len(buf) + len(sent) > size:
                chunks.append(Chunk(len(chunks) + 1, sec.name, _page_of(paper, buf, sec.page_start), buf))
                buf = buf[-150:] if len(buf) > 150 else ""  # small overlap keeps context across chunk borders
            buf = f"{buf} {sent}".strip()
        if len(buf) > 40:
            chunks.append(Chunk(len(chunks) + 1, sec.name, _page_of(paper, buf, sec.page_start), buf))
    return chunks


class PaperIndex:
    def __init__(self, paper: ParsedPaper):
        self.chunks = chunk_paper(paper)
        self._bm25 = BM25([f"{c.section} {c.text}" for c in self.chunks])

    def retrieve(self, query: str, k: int = 4) -> list[Chunk]:
        return [self.chunks[i] for i, _ in self._bm25.top(query, k)]

    def get(self, chunk_id: int) -> Optional[Chunk]:
        return self.chunks[chunk_id - 1] if 1 <= chunk_id <= len(self.chunks) else None
