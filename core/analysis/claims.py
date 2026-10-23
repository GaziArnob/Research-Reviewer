from core.analysis.context import CLAIMS_PLAN, build_context
from core.evidence.verifier import locate
from core.llm.base import LLMClient
from core.llm.jsonutil import ask_json
from core.models import Claim, ParsedPaper
from core.prompts import load


def extract_claims(paper: ParsedPaper, llm: LLMClient) -> tuple[list[Claim], int]:
    """Extract claims. A claim is kept only if its quote exists in the paper. Returns (claims, dropped)."""
    data = ask_json(llm, load("claims"), build_context(paper, CLAIMS_PLAN))
    if not data:
        return [], 0
    claims, dropped = [], 0
    for item in (data.get("claims") or [])[:8]:
        if not isinstance(item, dict):
            continue
        text, quote = str(item.get("text", "")).strip(), str(item.get("quote", "")).strip()
        hit = locate(paper, quote) if quote else None
        if not text or hit is None:
            dropped += 1
            continue
        ev, _ = hit
        claims.append(Claim(text=text, quote=ev.quote, section=ev.section, page=ev.page))
    return claims, dropped
