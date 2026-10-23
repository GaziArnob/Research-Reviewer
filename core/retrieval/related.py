import re
from concurrent.futures import ThreadPoolExecutor

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
    """OpenAlex's own related-works graph first, then keyword search in three free sources (run in parallel).
    Results are de-duplicated by normalized title and the paper itself is excluded."""
    seen = {re.sub(r"\W+", "", paper.title.lower())} if paper.title else set()
    tasks = []
    if paper.title:
        tasks.append(lambda: openalex.related_by_graph(paper.title, per_source * 2))
    for q in build_queries(paper):
        for source in (semantic_scholar, arxiv, openalex):
            tasks.append(lambda source=source, q=q: source.search(q, per_source))
    with ThreadPoolExecutor(max_workers=6) as pool:
        batches = list(pool.map(lambda t: t(), tasks))  # same order as tasks, so the result is deterministic
    out: list[RelatedPaper] = []
    for batch in batches:
        for p in batch:
            key = re.sub(r"\W+", "", p.title.lower())
            if key and key not in seen:
                seen.add(key)
                out.append(p)
    return out[:cap]
