from difflib import SequenceMatcher

from core.config import settings
from core.models import Evidence, Finding, ParsedPaper
from core.references import crossref


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def verify_references(paper: ParsedPaper) -> list[Finding]:
    """Check DOIs and titles against Crossref. A miss is a hint (coverage is incomplete), not proof of a fake."""
    findings: list[Finding] = []
    refs = paper.references[: settings.max_refs_to_verify]
    network_failed = 0
    unverified, bad_doi, retracted = [], [], []

    for ref in refs:
        if ref.doi:
            r = crossref.get_work(ref.doi)
            if r.status == 0:
                network_failed += 1
            elif r.status == 404:
                bad_doi.append(ref)
            elif r.status == 200 and r.data:
                if crossref.is_retracted(r.data.get("message", {})):
                    retracted.append(ref)
        elif ref.title:
            r = crossref.search_title(ref.title)
            if r.status == 0:
                network_failed += 1
                continue
            items = (r.data or {}).get("message", {}).get("items", []) if r.status == 200 else []
            best = max((_ratio(ref.title, (it.get("title") or [""])[0]) for it in items), default=0.0)
            if best < 0.8:
                unverified.append(ref)

    def ev(ref):
        return [Evidence(quote=ref.raw[:200], section="References")]

    for ref in bad_doi:
        findings.append(Finding(module="references", kind="major", status="needs_verification",
                                text=f"DOI {ref.doi} was not found in Crossref (reference {ref.key}).", evidence=ev(ref)))
    for ref in retracted:
        findings.append(Finding(module="references", kind="major", status="needs_verification",
                                text=f"Reference {ref.key} is marked as retracted or withdrawn in Crossref.", evidence=ev(ref)))
    if unverified:
        keys = ", ".join(r.key for r in unverified)
        findings.append(Finding(
            module="references", kind="minor", status="uncertain",
            text=(f"{len(unverified)} of {len(refs)} checked references ({keys}) could not be matched to a Crossref "
                  "record by title. Preprints, books and web pages are often missing from Crossref, so this is "
                  "a hint, not proof of a wrong reference."),
            evidence=[e for r in unverified[:5] for e in ev(r)]))
    if network_failed:
        findings.append(Finding(module="references", kind="flag", status="uncertain",
                                text=f"{network_failed} reference lookups failed (offline or rate-limited); results are incomplete."))
    return findings
