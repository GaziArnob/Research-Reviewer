import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

import numpy as np
from pydantic import BaseModel

from core.models import Finding

MODEL_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "calibration.json"
FEATURES = ["raw_overall", "n_major", "n_minor", "n_strength", "n_question"]
MIN_CV_SPEARMAN = 0.3  # a fitted model is only used if it generalises this well on held-out papers
LLM_MODULES = ("review", "critic", "novelty")


class CalibrationModel(BaseModel):
    features: list[str]
    mean: list[float]
    std: list[float]
    weights: list[float]
    intercept: float
    lam: float
    n: int
    cv_spearman: Optional[float] = None
    trained_on: str = ""


def feature_vector(raw_overall: float, findings: list[Finding]) -> list[float]:
    f = [x for x in findings if x.module in LLM_MODULES]
    return [float(raw_overall),
            float(sum(x.kind == "major" for x in f)), float(sum(x.kind == "minor" for x in f)),
            float(sum(x.kind == "strength" for x in f)), float(sum(x.kind == "question" for x in f))]


def fit_ridge(X: np.ndarray, y: np.ndarray, lam: float) -> tuple[np.ndarray, float, np.ndarray, np.ndarray]:
    """Ridge regression on standardised features. Returns (weights, intercept, mean, std)."""
    mean, std = X.mean(axis=0), X.std(axis=0)
    std[std == 0] = 1.0  # a constant feature carries no information and gets weight 0
    Z = (X - mean) / std
    w = np.linalg.solve(Z.T @ Z + lam * np.eye(Z.shape[1]), Z.T @ (y - y.mean()))
    return w, float(y.mean()), mean, std


def predict_raw(w: np.ndarray, b: float, mean: np.ndarray, std: np.ndarray, X: np.ndarray) -> np.ndarray:
    return ((X - mean) / std) @ w + b


def loo_predictions(X: np.ndarray, y: np.ndarray, lam: float) -> np.ndarray:
    """Leave-one-out predictions: every paper is predicted by a model that never saw it."""
    out = np.empty(len(y))
    for i in range(len(y)):
        keep = np.arange(len(y)) != i
        w, b, m, s = fit_ridge(X[keep], y[keep], lam)
        out[i] = predict_raw(w, b, m, s, X[i:i + 1])[0]
    return out


def fit_and_save(X: list[list[float]], y: list[float], trained_on: str, path: Path = MODEL_PATH) -> dict:
    """Pick the ridge strength by leave-one-out Spearman, fit on all data and save. Returns a summary."""
    from eval.metrics import mae, spearman
    Xa, ya = np.asarray(X, dtype=float), np.asarray(y, dtype=float)
    best = None
    for lam in (0.1, 1.0, 5.0, 20.0, 100.0):
        pred = loo_predictions(Xa, ya, lam)
        r = spearman(pred, ya)
        score = -2 if r is None else r
        if best is None or score > best[0]:
            best = (score, lam, pred, r)
    _, lam, pred, r = best
    w, b, mean, std = fit_ridge(Xa, ya, lam)
    model = CalibrationModel(features=FEATURES, mean=mean.tolist(), std=std.tolist(), weights=w.tolist(),
                             intercept=b, lam=lam, n=len(ya), cv_spearman=r, trained_on=trained_on)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(model.model_dump_json(indent=2), encoding="utf-8")
    load_model.cache_clear()
    return {"lam": lam, "loo_spearman": r, "loo_mae": mae(pred, ya), "baseline_mae": mae([ya.mean()] * len(ya), ya),
            "weights": dict(zip(FEATURES, w.round(3).tolist())), "usable": r is not None and r >= MIN_CV_SPEARMAN}


@lru_cache(maxsize=1)
def load_model(path: Path = MODEL_PATH) -> Optional[CalibrationModel]:
    try:
        return CalibrationModel(**json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def apply_model(model: CalibrationModel, raw_overall: float, findings: list[Finding]) -> float:
    x = np.asarray([feature_vector(raw_overall, findings)])
    pred = predict_raw(np.asarray(model.weights), model.intercept, np.asarray(model.mean), np.asarray(model.std), x)[0]
    return float(min(10.0, max(1.0, pred)))
