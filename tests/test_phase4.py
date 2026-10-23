import numpy as np
import pytest

from core.models import Finding, Scores
from core.review import calibration, scoring
from core.review.calibration import CalibrationModel, apply_model, feature_vector, fit_and_save, loo_predictions
from eval.fetch_peerread import official_ratings
from eval.metrics import auc, spearman, spearman_ci
from eval.run_benchmark import pick_sample


def test_spearman_auc_and_undefined_cases():
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert spearman([1, 2, 2, 3], [1, 2, 2, 3]) == pytest.approx(1.0)  # ties
    assert spearman([5, 5, 5, 5], [1, 2, 3, 4]) is None  # constant model output: undefined, not 0
    assert spearman([1, 2], [1, 2]) is None
    assert auc([3, 4, 1, 2], [True, True, False, False]) == 1.0
    assert auc([1, 1], [True, False]) == 0.5 and auc([1, 2], [True, True]) is None
    lo, hi = spearman_ci(list(range(20)), [i + (i % 3) for i in range(20)])
    assert lo <= hi and hi <= 1.0


def test_official_ratings_ignores_commenters_and_chairs():
    data = {"reviews": [
        {"OTHER_KEYS": "ICLR 2017 conference AnonReviewer1", "RECOMMENDATION": 7, "IS_META_REVIEW": False},
        {"OTHER_KEYS": "ICLR 2017 conference AnonReviewer2", "RECOMMENDATION": 5, "IS_META_REVIEW": False},
        {"OTHER_KEYS": "Some Author", "RECOMMENDATION": 10, "IS_META_REVIEW": False},
        {"OTHER_KEYS": "ICLR 2017 pcs", "RECOMMENDATION": None, "IS_META_REVIEW": False},
        {"OTHER_KEYS": "ICLR 2017 conference AnonReviewer3", "RECOMMENDATION": None, "IS_META_REVIEW": False}]}
    assert official_ratings(data) == [7, 5]


def test_pick_sample_spreads_over_rating_range():
    papers = [{"id": str(i), "human_mean": i / 10} for i in range(50)]
    s = pick_sample(papers, 5)
    assert [p["id"] for p in s] == ["0", "12", "24", "37", "49"]  # evenly spaced ranks, lowest and highest included
    assert len(pick_sample(papers, None)) == 50


def test_ridge_calibration_recovers_signal_and_is_leave_one_out_honest(tmp_path, monkeypatch):
    rng = np.random.default_rng(1)
    raw = rng.uniform(3, 9, 40)
    human = 0.8 * raw + rng.normal(0, 0.4, 40)
    X = [[r, 0, 0, 0, 0] for r in raw]  # only the raw score carries information; other columns are constant
    path = tmp_path / "cal.json"
    summary = fit_and_save(X, list(human), "synthetic", path=path)
    assert summary["loo_spearman"] > 0.85 and summary["usable"] and summary["loo_mae"] < summary["baseline_mae"]
    assert summary["weights"]["n_major"] == 0.0  # constant feature gets no weight

    # noise-only data must NOT look good out of sample
    while True:  # a target that really is unrelated to the score (a random draw can correlate by chance)
        unrelated = rng.normal(6, 1, 40)
        if abs(spearman(raw, unrelated)) < 0.1:
            break
    noise = fit_and_save([[r, 0, 0, 0, 0] for r in raw], list(unrelated), "noise", path=tmp_path / "n.json")
    assert not noise["usable"]


def test_scoring_uses_fitted_model_only_when_it_generalises(tmp_path, monkeypatch):
    sc = Scores(technical=8, novelty=8, methodology=8, clarity=8, reproducibility=8, overall=8,
                recommendation="Accept", confidence=0.9, justification="")
    findings = [Finding(module="review", kind="major", text="a")]
    heuristic = scoring.calibrate(sc, findings)
    assert heuristic.calibrated is False and "Not calibrated" in heuristic.adjustment_note

    good = CalibrationModel(features=calibration.FEATURES, mean=[6, 0, 0, 0, 0], std=[1, 1, 1, 1, 1],
                            weights=[1.0, 0, 0, 0, 0], intercept=5.0, lam=1, n=40, cv_spearman=0.6, trained_on="test")
    monkeypatch.setattr(scoring, "load_model", lambda: good)
    used = scoring.calibrate(sc, findings)
    assert used.calibrated is True and used.overall == 7.0 and "40 papers" in used.adjustment_note  # (8-6)*1+5

    weak = good.model_copy(update={"cv_spearman": 0.1})
    monkeypatch.setattr(scoring, "load_model", lambda: weak)
    assert scoring.calibrate(sc, findings).calibrated is False
    assert apply_model(good, 99, []) == 10.0  # always clipped to the 1-10 scale
    assert feature_vector(7, findings)[:2] == [7.0, 1.0]
