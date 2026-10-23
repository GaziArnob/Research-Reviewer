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

    for span in paper.hidden_spans:
        findings.append(Finding(
            module="injection", kind="flag", status="verified",
            text=f"Hidden text on page {span.page} ({span.reason}); it was removed before analysis.",
            evidence=[Evidence(quote=span.text[:200], page=span.page)],
        ))

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
