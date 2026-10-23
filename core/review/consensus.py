from rapidfuzz import fuzz

from core.models import Finding

SAME_TEXT = 68  # token_set_ratio of two comment texts that make the same point
SAME_QUOTE = 85


def _same_point(a: Finding, b: Finding) -> bool:
    if a.kind != b.kind and not {a.kind, b.kind} <= {"major", "minor"}:
        return False
    if fuzz.token_set_ratio(a.text, b.text) >= SAME_TEXT:
        return True
    qa, qb = (a.evidence[0].quote if a.evidence else ""), (b.evidence[0].quote if b.evidence else "")
    return bool(qa and qb and fuzz.partial_ratio(qa.lower(), qb.lower()) >= SAME_QUOTE and a.kind == b.kind)


def merge_runs(runs: list[list[Finding]], min_votes: int = 2) -> list[Finding]:
    """Self-consistency: keep a comment only if at least `min_votes` independent runs made the same point.

    Hallucinated comments tend to differ from run to run, real ones repeat. `confidence` becomes the share of
    runs that agreed. Costs `len(runs)` times as much model time.
    """
    clusters: list[list[tuple[int, Finding]]] = []
    for ri, findings in enumerate(runs):
        for f in findings:
            for cl in clusters:
                if all(r != ri for r, _ in cl) and _same_point(cl[0][1], f):
                    cl.append((ri, f))
                    break
            else:
                clusters.append([(ri, f)])
    kept = []
    for cl in clusters:
        votes = len({r for r, _ in cl})
        if votes >= min_votes:
            rep = cl[0][1].model_copy(update={"confidence": round(votes / len(runs), 2)})
            if votes < len(runs) and rep.status == "verified":
                rep = rep.model_copy(update={"status": "needs_verification"})
            kept.append(rep)
    return kept
