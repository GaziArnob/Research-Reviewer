import math
from typing import Optional, Sequence

import numpy as np


def _ranks(x: Sequence[float]) -> np.ndarray:
    """Average ranks (ties share the mean rank)."""
    a = np.asarray(x, dtype=float)
    order = a.argsort(kind="mergesort")
    ranks = np.empty(len(a))
    i = 0
    while i < len(a):
        j = i
        while j + 1 < len(a) and a[order[j + 1]] == a[order[i]]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def pearson(x: Sequence[float], y: Sequence[float]) -> Optional[float]:
    a, b = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    if len(a) < 3 or a.std() == 0 or b.std() == 0:
        return None  # undefined: fewer than 3 points or one side is constant
    return float(np.corrcoef(a, b)[0, 1])


def spearman(x: Sequence[float], y: Sequence[float]) -> Optional[float]:
    return pearson(_ranks(x), _ranks(y))


def spearman_ci(x: Sequence[float], y: Sequence[float], n_boot: int = 2000, seed: int = 0) -> Optional[tuple[float, float]]:
    """95% bootstrap interval. With 20-40 papers it is wide; that width is part of the result."""
    a, b = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(n_boot):
        idx = rng.integers(0, len(a), len(a))
        r = spearman(a[idx], b[idx])
        if r is not None:
            vals.append(r)
    if len(vals) < n_boot // 2:
        return None
    return float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))


def auc(scores: Sequence[float], labels: Sequence[bool]) -> Optional[float]:
    """Probability that a random accepted paper scores higher than a random rejected one (ties count half)."""
    s, l = np.asarray(scores, dtype=float), np.asarray(labels, dtype=bool)
    pos, neg = s[l], s[~l]
    if len(pos) == 0 or len(neg) == 0:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return float(wins / (len(pos) * len(neg)))


def mae(pred: Sequence[float], truth: Sequence[float]) -> float:
    return float(np.mean(np.abs(np.asarray(pred, dtype=float) - np.asarray(truth, dtype=float))))


def fmt(v: Optional[float], nd: int = 2) -> str:
    return "undefined" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v:.{nd}f}"
