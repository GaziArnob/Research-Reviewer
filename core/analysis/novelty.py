from typing import Optional

from rapidfuzz import fuzz

from core.analysis.literature import rank_related
from core.evidence.verifier import MIN_QUOTE_CHARS, _norm
from core.llm.base import LLMClient
from core.llm.jsonutil import ask_json
from core.models import Claim, Evidence, Finding, ParsedPaper, RelatedPaper
from core.prompts import load

TOP_RELATED = 5


def _in_abstract(quote: str, abstract: str, threshold: int = 88) -> bool:
    q, a = _norm(quote), _norm(abstract)
    return len(q) >= MIN_QUOTE_CHARS and (q in a or (len(q) <= len(a) and fuzz.partial_ratio(q, a) >= threshold))


def assess_novelty(paper: ParsedPaper, related: list[RelatedPaper], claims: list[Claim],
                   understanding: Optional[dict], llm: LLMClient) -> tuple[list[Finding], int, bool]:
    """Ask the model which related abstracts overlap with the claimed contribution.

    Every overlap must quote the related abstract; quotes that are not in that abstract are dropped.
    Returns (findings, dropped, answered).
    """
    ranked = [r for r, _ in rank_related(paper, related) if r.abstract][:TOP_RELATED]
    if not ranked:
        return [], 0, True  # nothing to compare against; not a model failure

    abstract = paper.section("abstract")
    contributions = (understanding or {}).get("contributions") or [c.text for c in claims]
    desc = f"Title: {paper.title}\nAbstract: {(abstract.text if abstract else '')[:1500]}\n" \
           f"Claimed contributions: {'; '.join(contributions)[:800]}"
    shown = "\n".join(f"[R{i}] {r.title} ({r.year or 'n/a'}): {(r.abstract or '')[:700]}" for i, r in enumerate(ranked, 1))
    data = ask_json(llm, load("novelty"), f"<paper>\n{desc}\n</paper>\n\n<related>\n{shown}\n</related>")
    if not data:
        return [], 0, False

    findings, dropped = [], 0
    for item in (data.get("overlaps") or [])[:5]:
        if not isinstance(item, dict):
            continue
        try:
            rel = ranked[int(str(item.get("related", "")).strip("R[] ")) - 1]
        except (ValueError, IndexError):
            dropped += 1
            continue
        level = str(item.get("overlap", "")).lower()
        quote = str(item.get("quote", "")).strip()
        if level not in ("high", "medium") or not _in_abstract(quote, rel.abstract or ""):
            dropped += 1
            continue
        reason = str(item.get("reason", "")).strip()
        findings.append(Finding(
            module="novelty", kind="major" if level == "high" else "minor", status="needs_verification",
            source="llm", confidence=0.5,
            text=f'{level.capitalize()} overlap with "{rel.title}" ({rel.year or "n/a"}): {reason}',
            evidence=[Evidence(quote=quote[:300], section="Related paper abstract", source="related_work")]))
    return findings, dropped, True
