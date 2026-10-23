import re
from datetime import date

from core.models import Finding, ParsedPaper

REQUIRED = ["Abstract", "Introduction", "Conclusion", "References"]


def _f(kind, text, status="verified") -> Finding:
    return Finding(module="preflight", kind=kind, text=text, status=status)


def run_preflight(paper: ParsedPaper) -> list[Finding]:
    findings: list[Finding] = []

    names = {s.name.lower() for s in paper.sections}
    for req in REQUIRED:
        alt = {req.lower(), req.lower() + "s"}
        if not names & alt:
            findings.append(_f("minor", f"Section '{req}' was not detected.", "needs_verification"))

    abstract = paper.section("abstract")
    if abstract:
        n = len(re.findall(r"\w+", abstract.text))
        if n < 80 or n > 350:
            findings.append(_f("minor", f"Abstract has {n} words (typical range is 150-300)."))

    refs = paper.references
    if paper.section("references") and len(refs) < 10:
        findings.append(_f("minor", f"Only {len(refs)} references detected; related work may be thin."))
    years = [r.year for r in refs if r.year]
    if len(years) >= 10:
        old = sum(1 for y in years if y < date.today().year - 10)
        if old / len(years) > 0.5:
            findings.append(_f("minor", f"{old} of {len(years)} references are older than 10 years."))

    body = _without_captions(paper.body_text)
    for kind, numbers, label in (("Figure", paper.figure_numbers, "Fig(?:ure)?"), ("Table", paper.table_numbers, "Table")):
        unreferenced = sorted(set(numbers) - _mentioned(body, label))
        if unreferenced:
            names = ", ".join(str(n) for n in unreferenced)
            findings.append(_f("minor", f"{kind}s {names} have a caption but no reference in the text "
                                        "(they may be cited in an appendix or not at all).", "needs_verification"))
    return findings


_CAPTION_LINE = re.compile(r"^\s*(?:Figure|Fig\.|Table)\s*\d+\s*[:.]", re.I)
_NUM_LIST = r"(\d+[a-z]?(?:\s*(?:,|and|&|–|-)\s*\d+[a-z]?)*)"


def _without_captions(text: str) -> str:
    return "\n".join(l for l in text.splitlines() if not _CAPTION_LINE.match(l))


def _mentioned(text: str, label: str) -> set[int]:
    """Numbers cited anywhere as 'Figure 3', 'Figures 3 and 4', 'Fig. 2-4'."""
    found: set[int] = set()
    for m in re.finditer(rf"\b{label}s?\.?\s*{_NUM_LIST}", text, re.I):
        chunk = m.group(1)
        for a, b in re.findall(r"(\d+)\s*[–-]\s*(\d+)", chunk):
            if 0 <= int(b) - int(a) < 30:
                found.update(range(int(a), int(b) + 1))
        found.update(int(n) for n in re.findall(r"\d+", chunk))
    return found
