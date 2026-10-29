"""How well does the perplexity score separate AI text from human text? Uses data/benchmark/ai_samples.json (see eval.ai_samples).

Usage: python -m eval.ai_eval [--model full|quantized]
Prints AUC per measure and, for the chosen thresholds, the share of AI texts caught and of human texts wrongly flagged.
"""
import argparse
import json
import statistics

from core.checks import perplexity
from eval.overlap import BENCH


def auc(pos: list[float], neg: list[float]) -> float:
    """Chance that a random positive scores higher than a random negative (ties count half)."""
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def features(text: str) -> dict | None:
    r = perplexity.score_text(text)
    if r is None:
        return None
    ppls = sorted(s["ppl"] for s in r["sentences"])
    return {"doc_ppl": r["doc_ppl"], "doc_top10": r["doc_top10"], "median_ppl": statistics.median(ppls),
            "low_share": sum(p < 30 for p in ppls) / len(ppls), "sent_cv": statistics.pstdev(ppls) / statistics.mean(ppls) if len(ppls) > 2 else 0.0}


def run(prefer_full: bool) -> str:
    perplexity._state.clear()
    perplexity._load(prefer_full)
    data = json.loads((BENCH / "ai_samples.json").read_text(encoding="utf-8"))
    rows = []
    for d in data:
        f = features(d["text"])
        if f:
            rows.append((d["label"], d["kind"], f))
    ai = [f for l, _, f in rows if l == "ai"]
    hu = [f for l, _, f in rows if l == "human"]
    L = [f"### Model {perplexity._state['model_file']}: {len(ai)} AI texts, {len(hu)} human texts", "",
         "| Measure | AI median | Human median | AUC (1.0 = perfect, 0.5 = chance) |", "|---|---|---|---|"]
    for key, sign in (("doc_ppl", -1), ("median_ppl", -1), ("doc_top10", 1), ("low_share", 1), ("sent_cv", -1)):
        a, h = [sign * f[key] for f in ai], [sign * f[key] for f in hu]
        L.append(f"| {key} | {statistics.median(f[key] for f in ai):.2f} | {statistics.median(f[key] for f in hu):.2f} | {auc(a, h):.2f} |")
    for kind in ("human_paper", "human_review"):
        h = [-f["doc_ppl"] for l, k, f in rows if k == kind]
        L.append(f"| doc_ppl, AI vs {kind} only | | | {auc([-f['doc_ppl'] for f in ai], h):.2f} |")
    return "\n".join(L), rows


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="full", choices=["full", "quantized"])
    a = ap.parse_args()
    text, rows = run(a.model == "full")
    print(text)
    (BENCH / f"report_ai_{a.model}.md").write_text(text, encoding="utf-8")
    (BENCH / f"ai_features_{a.model}.json").write_text(json.dumps([[l, k, f] for l, k, f in rows]), encoding="utf-8")
