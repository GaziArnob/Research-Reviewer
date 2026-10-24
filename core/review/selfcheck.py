from core.llm.base import LLMClient
from core.llm.jsonutil import ask_json
from core.models import Finding
from core.prompts import load


def check_one(llm: LLMClient, finding: Finding) -> str:
    """'yes' | 'partly' | 'no' | 'unknown' (no quote to check, or no usable answer)."""
    quote = next((e.quote for e in finding.evidence if e.quote), "")
    if not quote:
        return "unknown"
    data = ask_json(llm, load("selfcheck"), f"COMMENT: {finding.text}\n\nQUOTE: {quote}", temperature=0.0)
    answer = str((data or {}).get("supports", "")).lower()
    return answer if answer in ("yes", "partly", "no") else "unknown"


def self_verify(findings: list[Finding], llm: LLMClient) -> tuple[list[Finding], dict]:
    """Ask the model, one short question per comment, whether the quote really supports it. A 'no' does not delete
    the comment: it is marked uncertain so the reader sees that the model doubted its own comment."""
    counts = {"yes": 0, "partly": 0, "no": 0, "unknown": 0}
    out = []
    for f in findings:
        verdict = check_one(llm, f) if f.source == "llm" and f.kind != "strength" else "unknown"
        counts[verdict] += 1
        if verdict == "no":
            f = f.model_copy(update={"status": "uncertain", "confidence": min(f.confidence, 0.3),
                                     "text": f.text + " (The model doubted that the quote supports this point.)"})
        out.append(f)
    return out, counts
