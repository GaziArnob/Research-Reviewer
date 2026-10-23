import math
import re
from collections import Counter

_STOP = set("a an and are as at be by for from has have in is it its of on or that the this to was were which with we our "
            "can not but also such these those their they than then into over more most other any all each both using used use".split())


def tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOP and len(t) > 1]


class BM25:
    """Small BM25 ranker. Free, offline, no model download; good for exact technical terms."""

    def __init__(self, docs: list[str], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tf = [Counter(tokenize(d)) for d in docs]
        self.len = [sum(c.values()) for c in self.tf]
        self.avg = (sum(self.len) / len(self.len)) if self.len else 0.0
        df: Counter = Counter()
        for c in self.tf:
            df.update(c.keys())
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query: str) -> list[float]:
        q = tokenize(query)
        out = []
        for tf, dl in zip(self.tf, self.len):
            s = 0.0
            for t in q:
                f = tf.get(t, 0)
                if f:
                    s += self.idf.get(t, 0.0) * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / (self.avg or 1)))
            out.append(s)
        return out

    def top(self, query: str, k: int = 5) -> list[tuple[int, float]]:
        ranked = sorted(enumerate(self.scores(query)), key=lambda x: x[1], reverse=True)
        return [(i, s) for i, s in ranked[:k] if s > 0]
