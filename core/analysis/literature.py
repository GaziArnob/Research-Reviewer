from collections import Counter

from rapidfuzz import fuzz

from core.evidence.verifier import _norm
from core.models import Finding, ParsedPaper, RelatedPaper
from core.retrieval.bm25 import BM25, tokenize


def _paper_query(paper: ParsedPaper) -> str:
    abstract = paper.section("abstract")
    return f"{paper.title} {abstract.text if abstract else ''}"[:2500]


def rank_related(paper: ParsedPaper, related: list[RelatedPaper]) -> list[tuple[RelatedPaper, float]]:
    """Related papers ordered by textual similarity to this paper's title + abstract (score 0-1)."""
    if not related:
        return []
    docs = [f"{r.title} {r.title} {r.abstract or ''}" for r in related]  # title counted twice: abstracts can be missing
    scores = BM25(docs).scores(_paper_query(paper))
    top = max(scores) or 1.0
    return sorted(((r, s / top) for r, s in zip(related, scores)), key=lambda x: x[1], reverse=True)


def uncited_similar(paper: ParsedPaper, related: list[RelatedPaper], limit: int = 3,
                    min_similarity: float = 0.6) -> list[Finding]:
    """Highly similar papers whose title does not appear in the reference list: possible missing related work."""
    section = paper.section("references")
    if not section or not related:
        return []
    refs_text = _norm(section.text)
    own_terms = set(tokenize(_paper_query(paper)))
    findings = []
    for r, sim in rank_related(paper, related):
        if sim < min_similarity or len(findings) >= limit:
            continue
        # The BM25 score is relative to the best search hit, which can still be unrelated. Also require
        # that most of the candidate's title words really occur in this paper's title/abstract.
        words = set(tokenize(r.title))
        shared = words & own_terms
        if len(shared) < 3 or len(shared) / max(len(words), 1) < 0.5:
            continue
        cited = len(_norm(r.title)) >= 15 and fuzz.partial_ratio(_norm(r.title), refs_text) >= 88
        if cited:
            continue
        meta = ", ".join(x for x in (str(r.year) if r.year else "", r.venue or "") if x)
        findings.append(Finding(
            module="literature", kind="minor", status="uncertain", source="rule", confidence=0.4,
            text=f'Possibly missing related work: "{r.title}"{f" ({meta})" if meta else ""}. It is very similar '
                 "to this paper's title/abstract but was not found in the reference list. It may be the paper's own "
                 "earlier version or simply cited under a different title."))
    return findings


def publication_trends(paper: ParsedPaper, related: list[RelatedPaper]) -> dict:
    """Year histograms of the related papers found and of the paper's own references."""
    rel = Counter(r.year for r in related if r.year)
    refs = Counter(r.year for r in paper.references if r.year)
    return {"related_by_year": dict(sorted(rel.items())), "references_by_year": dict(sorted(refs.items()))}
