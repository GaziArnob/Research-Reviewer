import re

from core.models import Finding, ParsedPaper

_NUM_CITE = re.compile(r"\[(\d+(?:\s*[,–\-]\s*\d+)*)\]")


def cited_numbers(text: str) -> set[int]:
    out: set[int] = set()
    for m in _NUM_CITE.finditer(text):
        for part in re.split(r"\s*,\s*", m.group(1)):
            rng = re.split(r"\s*[–\-]\s*", part)
            if len(rng) == 2 and rng[0].isdigit() and rng[1].isdigit() and int(rng[1]) - int(rng[0]) < 50:
                out.update(range(int(rng[0]), int(rng[1]) + 1))
            elif part.strip().isdigit():
                out.add(int(part))
    return out


def match_citations(paper: ParsedPaper) -> list[Finding]:
    """Match numeric in-text citations against the reference list."""
    cited = cited_numbers(paper.body_text)
    listed = {int(r.key) for r in paper.references if r.key.isdigit()}
    if not cited or not listed:
        if paper.references:
            return [Finding(module="citations", kind="flag", status="uncertain",
                            text="Numeric citation style not detected; in-text to reference-list matching was skipped.")]
        return []

    findings = []
    missing = sorted(cited - listed)
    if missing:
        findings.append(Finding(module="citations", kind="major", status="verified",
                                text=f"In-text citations with no matching reference entry: {missing[:20]}."))
    uncited = sorted(listed - cited)
    if uncited:
        findings.append(Finding(module="citations", kind="minor", status="needs_verification",
                                text=f"References never cited in the text: {uncited[:20]}."))
    return findings
