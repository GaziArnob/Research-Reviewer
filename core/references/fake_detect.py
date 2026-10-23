from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher

from core.config import settings
from core.models import Evidence, Finding, ParsedPaper
from core.references import crossref


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _check_one(ref) -> tuple[str, object]:
    """One reference against Crossref. Returns (outcome, ref): ok | bad_doi | retracted | unmatched | network."""
    if ref.doi:
        r = crossref.get_work(ref.doi)
        if r.status == 0:
            return "network", ref
        if r.status == 404:
            return "bad_doi", ref
        if r.status == 200 and r.data and crossref.is_retracted(r.data.get("message", {})):
            return "retracted", ref
        return "ok", ref
    if ref.title:
        r = crossref.search_title(ref.title)
        if r.status == 0:
            return "network", ref
        items = (r.data or {}).get("message", {}).get("items", []) if r.status == 200 else []
        best = max((_ratio(ref.title, (it.get("title") or [""])[0]) for it in items), default=0.0)
        return ("ok" if best >= 0.8 else "unmatched"), ref
    return "ok", ref


def verify_references(paper: ParsedPaper, workers: int = 8) -> list[Finding]:
    """Check DOIs and titles against Crossref (several lookups at a time). A miss is a hint, not proof of a fake."""
    findings: list[Finding] = []
    refs = paper.references[: settings.max_refs_to_verify]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(_check_one, refs))  # keeps the reference order
    network_failed = sum(1 for o, _ in results if o == "network")
    unverified = [r for o, r in results if o == "unmatched"]
    bad_doi = [r for o, r in results if o == "bad_doi"]
    retracted = [r for o, r in results if o == "retracted"]

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
