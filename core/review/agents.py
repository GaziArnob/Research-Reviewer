import re

from rapidfuzz import fuzz

from core.analysis.context import REVIEW_PLAN, build_context
from core.evidence.verifier import verify_comments
from core.llm.base import LLMClient
from core.llm.jsonutil import ask_json
from core.models import Finding, ParsedPaper
from core.prompts import load


def run_reviewer(paper: ParsedPaper, llm: LLMClient) -> tuple[list[Finding], int]:
    """One reviewer pass. Unsupported comments are removed by the evidence verifier."""
    data = ask_json(llm, load("review"), build_context(paper, REVIEW_PLAN))
    if not data:
        return [], 0
    items = []
    for typ, key in (("strength", "strengths"), ("weakness", "weaknesses"), ("question", "questions")):
        for it in (data.get(key) or [])[:6]:
            if isinstance(it, dict):
                items.append({**it, "type": typ})
    return verify_comments(paper, items, module="review")


def run_critic(paper: ParsedPaper, llm: LLMClient, existing: list[Finding]) -> tuple[list[Finding], int, bool]:
    """Second pass that only looks for weaknesses. Duplicates of existing comments are skipped
    silently; comments without a real quote are dropped and counted. The last value says whether
    the model gave a usable answer at all (so an empty result can still be cached)."""
    listed = "\n".join(f"- {f.text}" for f in existing if f.kind in ("major", "minor", "strength")) or "(none)"
    user = f"{build_context(paper, REVIEW_PLAN)}\n\nExisting comments (do not repeat):\n{listed}"
    data = ask_json(llm, load("critic"), user)
    if not data:
        return [], 0, False
    items = [{**it, "type": "weakness"} for it in (data.get("weaknesses") or [])[:8] if isinstance(it, dict)]
    findings, dropped = verify_comments(paper, items, module="critic")
    known = [f.text for f in existing]
    praise = [f.text for f in existing if f.kind == "strength"]
    fresh = []
    for f in findings:
        if any(fuzz.token_set_ratio(f.text, k) >= 80 for k in known + [x.text for x in fresh]):
            continue
        # Small models often restate the authors' claims as "weaknesses"; those are praise in disguise.
        if any(fuzz.token_set_ratio(f.text, p) >= 65 for p in praise) or _restates_claim(f.text):
            dropped += 1
            continue
        fresh.append(f)
    return fresh, dropped, True


_RESTATE = re.compile(r"^the (?:paper|authors?|transformer|model)\b.{0,40}\b(?:claims?|states?|mentions?|says?|shows?|reports?|presents?|achieves)\b", re.I)
_CRITICAL = re.compile(r"\b(?:however|but|only|lack|lacks|missing|not|no|limited|unclear|without|fails?|single|unsupported|"
                       r"overclaim|insufficient|does not|doesn't)\b", re.I)


def _restates_claim(text: str) -> bool:
    return bool(_RESTATE.search(text)) and not _CRITICAL.search(text)
