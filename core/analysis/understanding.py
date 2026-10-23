from typing import Optional

from core.analysis.context import UNDERSTANDING_PLAN, build_context
from core.llm.base import LLMClient
from core.llm.jsonutil import ask_json
from core.models import ParsedPaper
from core.prompts import load

KEYS = ("summary", "problem", "objectives", "contributions", "key_findings", "conclusion")


def summarize(paper: ParsedPaper, llm: LLMClient) -> Optional[dict]:
    data = ask_json(llm, load("understanding"), build_context(paper, UNDERSTANDING_PLAN))
    if not data:
        return None
    out = {}
    for k in KEYS:
        v = data.get(k, [] if k in ("objectives", "contributions", "key_findings") else "")
        if k in ("objectives", "contributions", "key_findings"):
            v = [str(x) for x in v] if isinstance(v, list) else [str(v)]
        else:
            v = str(v)
        out[k] = v
    return out
