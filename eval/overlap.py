"""Does the tool say what human reviewers said? A lexical overlap measure, with a chance baseline.

For every sentence of the official human reviews we ask whether some tool finding on the SAME paper is similar to it
(recall), and for every tool finding whether some human sentence is similar (precision). The same numbers are computed
against the human reviews of OTHER, randomly chosen papers: that is the chance level a generic comment would reach.
Only the difference between the two means something. Similarity is TF-IDF cosine on words, so paraphrases are missed:
the numbers are a lower bound, good for comparing two versions of the tool, not an absolute quality score.
"""
import json
import math
import random
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable

from core.retrieval.bm25 import tokenize

BENCH = Path(__file__).resolve().parent.parent / "data" / "benchmark"
THRESHOLDS = (0.25, 0.35)


_CRITIQUE = re.compile(
    r"\b(?:however|but|lack(?:s|ing)?|unclear|not clear|missing|does not|do not|did not|doesn't|should|could|would be|"
    r"limited|only|unconvincing|weak(?:ness)?|concern|not (?:convinced|sure|enough|well)|no (?:comparison|evidence|analysis)|"
    r"insufficient|fail(?:s|ed)?|why|how|what|unfortunately|confus|question|suggest|need(?:s)?)\b", re.I)
_SUMMARY = re.compile(r"^\s*(?:this|the) (?:paper|work|authors?|manuscript|article)\s+(?:propos|present|introduc|describ|studi|investigat|develop|address)", re.I)


def is_critique(sentence: str) -> bool:
    """Heuristic: a sentence that points at a problem or asks something, not one that restates the paper."""
    return bool(_CRITIQUE.search(sentence)) and not _SUMMARY.search(sentence)


def human_sentences(paper_id: str, root: Path = BENCH, critique_only: bool = False) -> list[str]:
    f = root / "reviews" / f"{paper_id}.json"
    if not f.exists():
        return []
    out = []
    for comment in json.loads(f.read_text(encoding="utf-8"))["comments"]:
        for s in re.split(r"(?<=[.!?])\s+|\n+", comment or ""):
            if len(s.split()) >= 8 and (not critique_only or is_critique(s)):
                out.append(s.strip())
    return out


class Tfidf:
    def __init__(self, docs: Iterable[str]):
        df: Counter = Counter()
        n = 0
        for d in docs:
            df.update(set(tokenize(d)))
            n += 1
        self.idf = {t: math.log((1 + n) / (1 + c)) + 1 for t, c in df.items()}

    def vec(self, text: str) -> dict[str, float]:
        tf = Counter(tokenize(text))
        v = {t: (1 + math.log(c)) * self.idf.get(t, 1.0) for t, c in tf.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {t: x / norm for t, x in v.items()}


def cosine(a: dict, b: dict) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(t, 0.0) for t, x in a.items())


def _best(vs: list[dict], others: list[dict]) -> list[float]:
    return [max((cosine(v, o) for o in others), default=0.0) for v in vs]


def evaluate(tool: dict[str, list[tuple[str, str]]], human: dict[str, list[str]], seed: int = 0, chance_rounds: int = 20) -> dict:
    """tool: paper id -> [(module, finding text)], human: paper id -> sentences."""
    ids = [i for i in tool if human.get(i)]
    tf = Tfidf([s for i in ids for s in human[i]] + [t for i in ids for _, t in tool[i]])
    hv = {i: [tf.vec(s) for s in human[i]] for i in ids}
    tv = {i: [(m, tf.vec(t)) for m, t in tool[i]] for i in ids}
    rng = random.Random(seed)

    def scores(pairing: dict[str, str]):
        rec, prec, by_mod = defaultdict(list), defaultdict(list), defaultdict(list)
        for i in ids:
            j = pairing[i]
            tool_vecs = [v for _, v in tv[i]]
            h_best = _best(hv[j], tool_vecs) if tool_vecs else [0.0] * len(hv[j])
            t_best = _best(tool_vecs, hv[j])
            for th in THRESHOLDS:
                rec[th] += [b >= th for b in h_best]
                prec[th] += [b >= th for b in t_best]
            for (m, _), b in zip(tv[i], t_best):
                by_mod[m].append(b >= THRESHOLDS[0])
        mean = lambda xs: sum(xs) / len(xs) if xs else 0.0
        return ({th: mean(rec[th]) for th in THRESHOLDS}, {th: mean(prec[th]) for th in THRESHOLDS},
                {m: (mean(v), len(v)) for m, v in by_mod.items()})

    real_rec, real_prec, real_mod = scores({i: i for i in ids})
    ch_rec, ch_prec, ch_mod = defaultdict(float), defaultdict(float), defaultdict(float)
    for _ in range(chance_rounds):
        others = {i: rng.choice([j for j in ids if j != i]) for i in ids} if len(ids) > 1 else {i: i for i in ids}
        r, p, m = scores(others)
        for th in THRESHOLDS:
            ch_rec[th] += r[th] / chance_rounds
            ch_prec[th] += p[th] / chance_rounds
        for k, (v, _) in m.items():
            ch_mod[k] += v / chance_rounds
    return {"papers": len(ids), "tool_findings": sum(len(v) for v in tool.values()),
            "human_sentences": sum(len(v) for v in human.values()),
            "recall": real_rec, "recall_chance": dict(ch_rec), "precision": real_prec, "precision_chance": dict(ch_prec),
            "by_module": {m: {"match_rate": v, "n": n, "chance": ch_mod.get(m, 0.0)} for m, (v, n) in real_mod.items()}}


def format_report(name: str, r: dict) -> str:
    L = [f"### {name}", "", f"{r['papers']} papers, {r['tool_findings']} tool findings, {r['human_sentences']} human review sentences.", "",
         "| Similarity threshold | Recall (human points the tool covers) | Chance | Precision (tool points a human also made) | Chance |", "|---|---|---|---|---|"]
    for th in THRESHOLDS:
        L.append(f"| {th} | {r['recall'][th]:.1%} | {r['recall_chance'][th]:.1%} | {r['precision'][th]:.1%} | {r['precision_chance'][th]:.1%} |")
    L += ["", "| Check | Findings | Match rate | Chance |", "|---|---|---|---|"]
    for m, d in sorted(r["by_module"].items(), key=lambda kv: -kv[1]["n"]):
        L.append(f"| {m} | {d['n']} | {d['match_rate']:.1%} | {d['chance']:.1%} |")
    return "\n".join(L)
