import re

from core.models import ParsedPaper, RelatedPaper
from core.retrieval import arxiv, openalex, semantic_scholar


def build_queries(paper: ParsedPaper) -> list[str]:
    queries = []
    if paper.title:
        queries.append(paper.title)
    abstract = paper.section("abstract")
    if abstract:
        first = re.split(r"(?<=[.!?])\s+", " ".join(abstract.text.split()))[0]
        if len(first) > 30:
            queries.append(first[:250])
    return queries


def find_related(paper: ParsedPaper, per_source: int = 6, cap: int = 15) -> list[RelatedPaper]:
    """Search several free sources and de-duplicate by normalized title. The paper itself is excluded."""
    seen = {re.sub(r"\W+", "", paper.title.lower())} if paper.title else set()
    out: list[RelatedPaper] = []
    for q in build_queries(paper):
        for source in (semantic_scholar, arxiv, openalex):
            for p in source.search(q, per_source):
                key = re.sub(r"\W+", "", p.title.lower())
                if key and key not in seen:
                    seen.add(key)
                    out.append(p)
    return out[:cap]
