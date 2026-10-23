from typing import Optional

from core.llm.base import LLMClient
from core.llm.jsonutil import ask_json
from core.models import Finding, Scores
from core.prompts import load
from core.review.calibration import MIN_CV_SPEARMAN, apply_model, load_model

RECOMMENDATIONS = ["Accept", "Weak Accept", "Borderline", "Weak Reject", "Reject"]
DIMENSIONS = ("technical", "novelty", "methodology", "clarity", "reproducibility")


def recommendation_for(overall: float) -> str:
    if overall >= 8:
        return "Accept"
    if overall >= 7:
        return "Weak Accept"
    if overall >= 5:
        return "Borderline"
    if overall >= 4:
        return "Weak Reject"
    return "Reject"


SHRINK = 0.7  # keep 70% of the distance from the middle of the scale
MAJOR_PENALTY = 0.5
MAX_PENALISED_MAJORS = 3


def calibrate(sc: Scores, findings: list[Finding]) -> Scores:
    """Conservative heuristic: pull scores toward 5.5 (LLMs inflate) and subtract 0.5 per major weakness.

    This is NOT calibration against human data (that needs the OpenReview benchmark, planned later),
    so `calibrated` stays False and the note says so.
    """
    model = load_model()
    if model is not None and model.cv_spearman is not None and model.cv_spearman >= MIN_CV_SPEARMAN:
        overall = round(apply_model(model, sc.overall, findings), 1)
        note = (f"Calibrated on {model.n} papers ({model.trained_on}); leave-one-out Spearman "
                f"{model.cv_spearman:.2f}. Still a statistical estimate for a human reviewer to check.")
        return sc.model_copy(update={"overall": overall, "recommendation": recommendation_for(overall),
                                     "raw_overall": sc.overall, "adjustment_note": note, "calibrated": True})

    def shrink(v: float) -> float:
        return 5.5 + SHRINK * (v - 5.5)

    dims = {d: int(round(min(10, max(1, shrink(getattr(sc, d)))))) for d in DIMENSIONS}
    majors = sum(1 for f in findings if f.module in ("review", "critic") and f.kind == "major")
    overall = round(min(10.0, max(1.0, shrink(sc.overall) - MAJOR_PENALTY * min(majors, MAX_PENALISED_MAJORS))), 1)
    note = (f"Heuristic adjustment: scores pulled {int((1 - SHRINK) * 100)}% toward 5.5 and "
            f"{MAJOR_PENALTY} subtracted per major weakness (max {MAX_PENALISED_MAJORS}; {majors} found). "
            "Not calibrated against human reviews.")
    return sc.model_copy(update={**dims, "overall": overall, "recommendation": recommendation_for(overall),
                                 "raw_overall": sc.overall, "adjustment_note": note})


def _score(v) -> Optional[int]:
    try:
        n = int(round(float(v)))
    except (TypeError, ValueError):
        return None
    return n if 1 <= n <= 10 else None


def draft_scores(understanding: Optional[dict], findings: list[Finding], llm: LLMClient) -> Optional[Scores]:
    """Draft scores from the verified comments only. The result is a draft for a human to approve."""
    lines = [f"[{f.kind}] {f.text}" for f in findings if f.module == "review"]
    summary = (understanding or {}).get("summary", "")
    user = f"Paper summary: {summary}\n\nVerified review comments:\n" + ("\n".join(lines) or "(none)")
    data = ask_json(llm, load("scoring"), user)
    if not data:
        return None
    dims = {d: _score(data.get(d)) for d in DIMENSIONS}
    overall = _score(data.get("overall"))
    if overall is None or any(v is None for v in dims.values()):
        return None
    rec = recommendation_for(overall)  # derived from the score so the two can never disagree
    try:
        conf = min(max(float(data.get("confidence", 0.5)), 0.0), 1.0)
    except (TypeError, ValueError):
        conf = 0.5
    return Scores(**dims, overall=float(overall), recommendation=rec, confidence=conf,
                  justification=str(data.get("justification", ""))[:800], calibrated=False)
