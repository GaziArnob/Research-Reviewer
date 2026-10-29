"""Build a small test set for the AI-text check: AI passages written by the local model, and human passages from the
benchmark (papers and official reviews from 2016-17, written before language models existed).

Usage: python -m eval.ai_samples [--n 24]   -> data/benchmark/ai_samples.json  (gitignored)
The AI side is only text from the local Qwen model, so the numbers say how well the detector finds THAT kind of text,
not text from ChatGPT or Claude. Treat results as an upper bound.
"""
import argparse
import json
import random

from core.config import settings
from core.ingest.parser import parse_pdf
from core.llm.router import get_llm
from eval.overlap import BENCH

TOPICS = ["graph neural networks for molecule property prediction", "reinforcement learning with sparse rewards", "image classification with small datasets",
          "neural machine translation for low-resource languages", "federated learning and privacy", "speech recognition with self-supervised features",
          "anomaly detection in time series", "language model fine-tuning with limited data", "object detection in aerial images",
          "recommendation systems with implicit feedback", "medical image segmentation", "text summarisation of long documents"]
KINDS = [("Write the Introduction section of a research paper on {t}. About 250 words, plain paragraphs, no headings.", "ai_intro"),
         ("Write the Related Work section of a research paper on {t}. About 250 words, plain paragraphs, no headings.", "ai_related"),
         ("You are a peer reviewer. Write the weaknesses part of a review for a paper on {t}. About 200 words, plain paragraphs.", "ai_review")]


def window(words: list[str], n: int, rng: random.Random) -> str:
    if len(words) <= n:
        return " ".join(words)
    s = rng.randrange(0, len(words) - n)
    return " ".join(words[s:s + n])


def build(n: int) -> list[dict]:
    rng = random.Random(1)
    out = []
    llm = get_llm()
    for i in range(n):
        prompt, kind = KINDS[i % len(KINDS)]
        topic = TOPICS[i % len(TOPICS)]
        res = llm.complete("You write clear academic English.", prompt.format(t=topic), temperature=0.7)
        out.append({"label": "ai", "kind": kind, "text": res.text.strip()})
        print("ai", i + 1, "/", n, flush=True)
    pdfs = sorted((BENCH / "pdfs").glob("*.pdf"))
    rng.shuffle(pdfs)
    for f in pdfs[:n]:
        body = parse_pdf(f.read_bytes(), f.name).body_text
        # the middle of the paper: prose, not title block or tables
        words = [w for w in body.split()]
        mid = words[len(words) // 4: 3 * len(words) // 4]
        out.append({"label": "human", "kind": "human_paper", "text": window(mid, 250, rng)})
    rev = sorted((BENCH / "reviews").glob("*.json"))
    rng.shuffle(rev)
    for f in rev[:n]:
        text = " ".join(json.loads(f.read_text(encoding="utf-8"))["comments"])
        out.append({"label": "human", "kind": "human_review", "text": window(text.split(), 200, rng)})
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=24)
    a = ap.parse_args()
    data = build(a.n)
    (BENCH / "ai_samples.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print("saved", len(data), "samples with model", getattr(get_llm(), "model", "?"), "provider", settings.llm_provider)
