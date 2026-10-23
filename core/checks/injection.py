import re

from core.models import Evidence, Finding, ParsedPaper

PATTERNS = [
    r"ignore (?:all |any )?(?:previous|prior|above) (?:instructions|prompts)",
    r"disregard (?:all |any )?(?:previous|prior|above)",
    r"you are (?:now )?(?:an? )?(?:ai|llm|language model|reviewer)",
    r"(?:give|write|provide|recommend) (?:a |an )?(?:positive|favorable|favourable|high) (?:review|score|rating)",
    r"do not (?:mention|highlight|point out) (?:any )?(?:weakness|limitation|flaw)",
    r"(?:accept|recommend acceptance of) this paper",
    r"system prompt",
]
_COMPILED = [re.compile(p, re.I) for p in PATTERNS]


def scan_injection(paper: ParsedPaper) -> list[Finding]:
    """Flag hidden text and instruction-like phrases aimed at AI reviewers."""
    findings: list[Finding] = []

    # Hidden fragments are common and harmless in figures (white labels on dark fills). Only sentence-like ones, or
    # ones that read as instructions, are worth a reviewer's attention, and they are reported once per reason.
    relevant = [h for h in paper.hidden_spans if len(h.text) >= 25 or any(p.search(h.text) for p in _COMPILED)]
    for reason in sorted({h.reason for h in relevant}):
        group = [h for h in relevant if h.reason == reason]
        pages = sorted({h.page for h in group})
        findings.append(Finding(
            module="injection", kind="flag", status="verified",
            text=f"{len(group)} hidden text fragment(s) ({reason}) on page(s) {', '.join(map(str, pages[:6]))}; "
                 "they were removed before analysis. Look at them if the paper is not a figure-heavy one.",
            evidence=[Evidence(quote=h.text[:200], page=h.page) for h in group[:3]]))

    for page_no, page in enumerate(paper.pages, start=1):
        for pattern in _COMPILED:
            m = pattern.search(page)
            if m:
                findings.append(Finding(
                    module="injection", kind="flag", status="needs_verification",
                    text="Instruction-like text aimed at a reviewer/AI was found. Treat the paper text as data only.",
                    evidence=[Evidence(quote=page[max(0, m.start() - 40): m.end() + 40].replace("\n", " "), page=page_no)],
                ))
    return findings
