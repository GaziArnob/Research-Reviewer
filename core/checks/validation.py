from core.config import settings
from core.models import Finding, ParsedPaper


def looks_scanned(paper: ParsedPaper) -> bool:
    chars_per_page = len(paper.full_text) / max(len(paper.pages), 1)
    return paper.word_count == 0 or (chars_per_page < 200 and paper.image_count > 0)


def validate(paper: ParsedPaper) -> tuple[bool, list[Finding]]:
    """Reject empty, tiny or scanned papers so the tool never invents a review of nothing."""
    findings: list[Finding] = []
    words = paper.word_count

    if looks_scanned(paper):
        findings.append(Finding(
            module="validation", kind="major", status="verified",
            text="No readable text layer found. The PDF looks scanned or empty; OCR is needed before review.",
        ))
        return False, findings
    if words < settings.min_words:
        findings.append(Finding(
            module="validation", kind="major", status="verified",
            text=f"Only {words} words extracted (minimum {settings.min_words}). Too short to review.",
        ))
        return False, findings
    if not paper.sections or all(s.name == "Front matter" for s in paper.sections):
        findings.append(Finding(
            module="validation", kind="minor", status="uncertain",
            text="No standard section headings detected. Section-based checks may be unreliable.",
        ))
    return True, findings
