import re
from typing import Optional

from core.analysis.context import REVIEW_PLAN, build_context
from core.evidence.verifier import locate
from core.llm.base import LLMClient
from core.llm.jsonutil import ask_json
from core.models import Finding, ParsedPaper
from core.prompts import load

LIST_FORMAT = '{"items": [{"name": "short name", "quote": "verbatim sentence from the paper that names it"}]}'
YESNO_FORMAT = '{"answer": "yes | no | unclear", "quote": "verbatim sentence proving a yes, else empty"}'

QUESTIONS = {
    "datasets": f"List every dataset or benchmark the authors evaluate their method on. Format: {LIST_FORMAT}",
    "baselines": f"List every existing method or system the authors compare their results against. Format: {LIST_FORMAT}",
    "variance": f"Does the paper report variance, standard deviation, confidence intervals or results over several runs? Format: {YESNO_FORMAT}",
    "ablation": f"Does the paper analyse the contribution of its components (ablation, removing or varying parts of the model)? Format: {YESNO_FORMAT}",
    "limitations": f"Does the paper discuss its own limitations or failure cases? Format: {YESNO_FORMAT}",
}

# If these appear anywhere in the paper, the model's "no" is not trusted (it may have missed a table or section).
SANITY = {
    "variance": re.compile(r"±|\bstd\b|standard deviation|standard error|confidence interval|variance|several runs|averaged over", re.I),
    "ablation": re.compile(r"ablat|model variations|we (?:vary|varied)|leave[- ]one[- ]out|removing (?:the )?", re.I),
    "limitations": re.compile(r"limitation|shortcoming|drawback", re.I),
}


def _f(kind: str, text: str, quote_ev=None, confidence: float = 0.5) -> Finding:
    return Finding(module="critic", kind=kind, text=text, evidence=[quote_ev] if quote_ev else [],
                   status="needs_verification", source="llm-checklist", confidence=confidence)


def _verified_items(paper: ParsedPaper, data: dict) -> list[tuple[str, object]]:
    out = []
    for it in (data.get("items") or [])[:10]:
        if isinstance(it, dict) and it.get("name") and it.get("quote"):
            hit = locate(paper, str(it["quote"]))
            if hit:
                out.append((str(it["name"]).strip(), hit[0]))
    return out


def run_checklist(paper: ParsedPaper, llm: LLMClient) -> tuple[list[Finding], int, bool]:
    """Ask small, factual questions one at a time and let CODE decide what is a weakness.

    A small local model cannot critique a paper, but it can list datasets or say whether variance is
    reported. Facts must carry verified quotes; "no" answers are cross-checked with keyword searches.
    Returns (findings, dropped, answered_any).
    """
    context = build_context(paper, REVIEW_PLAN)
    text = paper.full_text
    findings: list[Finding] = []
    dropped, answered = 0, False

    for key, question in QUESTIONS.items():
        data = ask_json(llm, load("checklist"), f"{context}\n\nQUESTION: {key}. {question}")
        if not data:
            continue
        answered = True

        if key in ("datasets", "baselines"):
            raw = [i for i in (data.get("items") or []) if isinstance(i, dict)]
            items = _verified_items(paper, data)
            dropped += max(0, min(len(raw), 10) - len(items))
            label = "dataset or benchmark" if key == "datasets" else "baseline"
            if not raw:
                if key == "baselines":
                    findings.append(_f("minor", "No comparison against other methods was found "
                                                "(claim about something missing; verify manually).", None, 0.3))
                continue
            if len(items) == 1:
                name, ev = items[0]
                findings.append(_f("minor", f"Only one {label} was found ({name}); "
                                            "generality of the results may be limited.", ev, 0.6))
            continue

        answer = str(data.get("answer", "")).strip().lower()
        if answer == "no" and not SANITY[key].search(text):
            what = {"variance": "No variance, standard deviation or repeated-run results were found; "
                                "differences between methods may be within noise.",
                    "ablation": "No ablation or component analysis was found, so it is unclear which parts "
                                "of the method contribute to the results.",
                    "limitations": "The paper does not appear to discuss its own limitations."}[key]
            findings.append(_f("minor", what + " (claim about something missing; verify manually)", None, 0.4))
    return findings, dropped, answered
