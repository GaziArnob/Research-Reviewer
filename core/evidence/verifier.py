import re
from typing import Optional

from rapidfuzz import fuzz

from core.models import Evidence, Finding, ParsedPaper

MIN_QUOTE_CHARS = 20


def _norm(s: str) -> str:
    s = s.lower()
    s = re.sub(r"-\s*\n\s*", "", s)  # re-join words hyphenated across lines
    s = re.sub(r"[‘’]", "'", s)
    s = re.sub(r"[“”]", '"', s)
    return re.sub(r"\s+", " ", s).strip()


def locate(paper: ParsedPaper, quote: str, threshold: int = 88) -> Optional[tuple[Evidence, int]]:
    """Find a quote in the paper. Returns (Evidence with page/section, match score 0-100) or None."""
    q = _norm(quote)
    if len(q) < MIN_QUOTE_CHARS:
        return None

    best_score, best_page = 0, None
    for page_no, page in enumerate(paper.pages, start=1):
        n = _norm(page)
        score = 100 if q in n else (fuzz.partial_ratio(q, n) if len(q) <= len(n) else 0)
        if score > best_score:
            best_score, best_page = score, page_no
        if score == 100:
            break

    if best_score < threshold:
        # the quote may straddle a page break
        if q in _norm(paper.full_text):
            best_score, best_page = 100, None
        else:
            return None

    section = None
    for s in paper.sections:
        n = _norm(s.text)
        if q in n or (len(q) <= len(n) and fuzz.partial_ratio(q, n) >= threshold):
            section = s.name
            break
    return Evidence(quote=quote.strip()[:300], section=section, page=best_page), int(best_score)


def verify_comments(paper: ParsedPaper, items: list[dict], module: str,
                    source: str = "llm") -> tuple[list[Finding], int]:
    """Turn raw LLM comments into Findings. Comments whose quote is not in the paper are dropped.

    item keys: type (strength|weakness|question), text, quote, severity (major|minor), absence (bool).
    An absence claim ("no ablation study") cannot be quoted, so it is kept but labelled needs_verification.
    """
    findings: list[Finding] = []
    dropped = 0
    for it in items:
        text = (it.get("text") or "").strip()
        if not text:
            continue
        typ = it.get("type", "weakness")
        quote = (it.get("quote") or "").strip()

        if typ == "question":
            findings.append(Finding(module=module, kind="question", text=text,
                                    status="needs_verification", source=source, confidence=0.5))
            continue

        kind = "strength" if typ == "strength" else ("major" if it.get("severity") == "major" else "minor")

        if quote:
            hit = locate(paper, quote)
            if hit is None:
                dropped += 1
                continue
            ev, score = hit
            findings.append(Finding(module=module, kind=kind, text=text, evidence=[ev], source=source,
                                    status="verified" if score == 100 else "needs_verification",
                                    confidence=score / 100))
        elif it.get("absence") and typ == "weakness":
            # Absence claims cannot be quoted, so a model can assert them wrongly (a real run claimed
            # "no ablation study" about a paper with one). Never rate them above minor.
            findings.append(Finding(module=module, kind="minor", status="needs_verification", source=source,
                                    text=f"{text} (claim about something missing from the paper; verify manually)",
                                    confidence=0.4))
        else:
            dropped += 1
    return findings, dropped


def find_pattern(paper: ParsedPaper, pattern: str, before: int = 60, after: int = 100,
                 flags: int = re.I) -> Optional[Evidence]:
    """First match of a regex in the body text, returned as an Evidence with page and section when it can be located."""
    text = " ".join(paper.body_text.split())
    m = re.search(pattern, text, flags)
    if not m:
        return None
    snippet = text[max(0, m.start() - before): m.end() + after]
    hit = locate(paper, snippet)
    return hit[0] if hit else Evidence(quote=snippet[:260])
