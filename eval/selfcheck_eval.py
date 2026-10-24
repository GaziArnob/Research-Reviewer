"""Does the model's own check of 'does the quote support the comment' predict which comments human reviewers also made?

Usage: python -m eval.selfcheck_eval [--dir results_llm] [--papers 8]
For the LLM comments of some saved benchmark results: ask the self-check, then compare how often each answer
(yes / partly / no) matches a human critique sentence (same TF-IDF measure as eval.overlap). If "yes" does not
match more often than "no", the check carries no signal and should stay off.
"""
import argparse
import json

from core.llm.router import get_llm
from core.models import Finding
from core.review.selfcheck import check_one
from eval.overlap import BENCH, THRESHOLDS, Tfidf, cosine, human_sentences


def run(dir_name: str, n_papers: int) -> str:
    llm = get_llm()
    rows = []  # (answer, matched)
    files = sorted((BENCH / dir_name).glob("*.json"))[:n_papers]
    data = []
    for f in files:
        d = json.loads(f.read_text(encoding="utf-8"))
        human = human_sentences(d["paper"]["id"], critique_only=True)
        finds = [Finding(**x) for x in d["result"]["findings"] if x["module"] in ("review", "critic") and x["kind"] in ("major", "minor")]
        data.append((human, finds))
    tf = Tfidf([s for h, _ in data for s in h] + [x.text for _, fs in data for x in fs])
    for human, finds in data:
        hv = [tf.vec(s) for s in human]
        for fnd in finds:
            if not hv or not any(e.quote for e in fnd.evidence):
                continue
            v = tf.vec(fnd.text)
            rows.append((check_one(llm, fnd), max((cosine(v, h) for h in hv), default=0.0) >= THRESHOLDS[0]))
    L = [f"### Self-check vs human overlap ({dir_name}, {len(files)} papers, {len(rows)} comments)", "",
         "| Answer | Comments | Matched a human critique |", "|---|---|---|"]
    for a in ("yes", "partly", "no", "unknown"):
        sel = [m for ans, m in rows if ans == a]
        L.append(f"| {a} | {len(sel)} | {(sum(sel) / len(sel)):.1%} |" if sel else f"| {a} | 0 | - |")
    return "\n".join(L)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="results_llm")
    ap.add_argument("--papers", type=int, default=8)
    a = ap.parse_args()
    text = run(a.dir, a.papers)
    (BENCH / "report_selfcheck.md").write_text(text, encoding="utf-8")
    print(text)
