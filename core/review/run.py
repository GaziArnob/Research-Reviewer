import hashlib
from typing import Callable, Optional

from core.analysis.claims import extract_claims
from core.analysis.novelty import assess_novelty
from core.analysis.understanding import summarize
from core.llm.base import LLMClient
from core.models import Claim, Finding, ParsedPaper, PipelineResult, Scores
from core.prompts import PROMPT_VERSION
from core.config import settings
from core.review.agents import run_critic, run_reviewer
from core.review.consensus import merge_runs
from core.review.checklist import run_checklist
from core.review.scoring import calibrate, draft_scores
from core.storage.cache import Cache, make_key


def run_llm_review(paper: ParsedPaper, llm: LLMClient, cache: Cache, result: PipelineResult,
                   log: list[str]) -> None:
    """Phase 2 stages. Each stage is cached by (paper, step, prompt version, model)."""
    model = getattr(llm, "model", llm.name)
    result.llm_model = f"{llm.name}:{model}"

    def cached(step: str, compute: Callable[[], dict], ok: Callable[[dict], bool]) -> dict:
        key = make_key(paper.sha256, step, PROMPT_VERSION, result.llm_model)
        hit = cache.get(key)
        if hit is not None:
            log.append(f"{step}: cache hit")
            return hit
        value = compute()
        if ok(value):  # never cache a failed or empty model answer
            cache.set(key, value, paper.sha256, step)
        log.append(f"{step}: computed" if ok(value) else f"{step}: failed, not cached")
        return value

    understanding = cached("llm_understanding", lambda: {"u": summarize(paper, llm)},
                           lambda v: v["u"] is not None)["u"]
    result.understanding = understanding

    def claims_stage() -> dict:
        claims, dropped = extract_claims(paper, llm)
        return {"c": [c.model_dump() for c in claims], "dropped": dropped}

    c = cached("llm_claims", claims_stage, lambda v: bool(v["c"]) or v["dropped"] > 0)
    result.claims = [Claim(**x) for x in c["c"]]

    runs = max(1, settings.consensus_runs)

    def review_stage() -> dict:
        if runs == 1:
            findings, dropped = run_reviewer(paper, llm)
        else:  # several samples at a higher temperature; only points that at least two runs agree on are kept
            samples = [run_reviewer(paper, llm, temperature=0.7) for _ in range(runs)]
            findings = merge_runs([f for f, _ in samples], min_votes=max(2, (runs + 1) // 2))
            dropped = sum(d for _, d in samples)
        return {"f": [f.model_dump() for f in findings], "dropped": dropped}

    r = cached("llm_review" if runs == 1 else f"llm_review_c{runs}", review_stage, lambda v: bool(v["f"]) or v["dropped"] > 0)
    review_findings = [Finding(**x) for x in r["f"]]

    def critic_stage() -> dict:
        checked, d1, a1 = run_checklist(paper, llm)
        free, d2, a2 = run_critic(paper, llm, review_findings + checked)
        return {"f": [f.model_dump() for f in checked + free], "dropped": d1 + d2, "answered": a1 or a2}

    k = cached("llm_critic", critic_stage, lambda v: v["answered"])
    critic_findings = [Finding(**x) for x in k["f"]]
    all_review = review_findings + critic_findings

    related_key = hashlib.sha1("|".join(sorted(p.title for p in result.related)).encode()).hexdigest()[:10]

    def novelty_stage() -> dict:
        findings, dropped, answered = assess_novelty(paper, result.related, result.claims, understanding, llm)
        return {"f": [f.model_dump() for f in findings], "dropped": dropped, "answered": answered}

    nv = cached(f"llm_novelty_{related_key}", novelty_stage, lambda v: v["answered"])
    novelty_findings = [Finding(**x) for x in nv["f"]]
    result.findings += all_review + novelty_findings

    s = cached("llm_scores", lambda: {"s": (lambda sc: sc.model_dump() if sc else None)(
        draft_scores(understanding, all_review, llm))}, lambda v: v["s"] is not None)["s"]
    result.scores = calibrate(Scores(**s), all_review) if s else None  # heuristic, so computed after the cache

    result.stats["llm_comments_dropped"] = c["dropped"] + r["dropped"] + k["dropped"] + nv["dropped"]
    if understanding is None:
        log.append("warning: understanding step returned no valid JSON")
    if not review_findings:
        log.append("warning: no review comment survived evidence verification")
