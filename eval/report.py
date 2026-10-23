"""Compare the tool with the human reviewers and (optionally) fit a calibration model.

Usage: python -m eval.report [--dir results_llm] [--fit]
"""
import argparse
import json
from pathlib import Path

import numpy as np

from core.models import Finding, PipelineResult
from core.review.calibration import FEATURES, feature_vector, fit_and_save, loo_predictions
from eval.metrics import auc, fmt, mae, spearman, spearman_ci

BENCH = Path(__file__).resolve().parent.parent / "data" / "benchmark"


def load(dir_name: str) -> list[dict]:
    rows = []
    for f in sorted((BENCH / dir_name).glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        d["result"] = PipelineResult(**d["result"])
        rows.append(d)
    return rows


def rule_features(res: PipelineResult) -> dict[str, float]:
    rule = [f for f in res.findings if f.source == "rule"]
    return {"words": res.stats.get("words", 0), "references": res.stats.get("references", 0),
            "figures": res.stats.get("figures", 0), "tables": res.stats.get("tables", 0),
            "minor rule findings": sum(f.kind == "minor" for f in rule),
            "writing findings": sum(f.module == "writing" for f in rule),
            "reproducibility gaps": sum(f.module == "reproducibility" for f in rule)}


def build_report(rows: list[dict], do_fit: bool) -> str:
    ok = [r for r in rows if r["result"].status == "ok"]
    human = [r["paper"]["human_mean"] for r in ok]
    accepted = [r["paper"]["accepted"] for r in ok]
    L = [f"# Benchmark report\n", f"Papers: {len(rows)} run, {len(ok)} readable. Human scale: ICLR 2017 reviewer "
         f"recommendation (1-10), mean of 2+ official reviewers per paper.\n"]

    L.append("## Rule-based features vs human rating (Spearman)\n")
    L.append("| Feature | Spearman with human mean |\n|---|---|")
    feats = [rule_features(r["result"]) for r in ok]
    for name in feats[0] if feats else []:
        L.append(f"| {name} | {fmt(spearman([f[name] for f in feats], human))} |")
    L.append(f"\nHuman ratings: mean {np.mean(human):.2f}, sd {np.std(human):.2f}; accepted {sum(accepted)} of {len(ok)}.\n")

    scored = [r for r in ok if r["result"].scores]
    if not scored:
        L.append("No LLM scores in this run, so score agreement cannot be computed.\n")
        return "\n".join(L)

    h = [r["paper"]["human_mean"] for r in scored]
    acc = [r["paper"]["accepted"] for r in scored]
    raw = [r["result"].scores.raw_overall for r in scored]
    adj = [r["result"].scores.overall for r in scored]
    L.append(f"## LLM scores ({len(scored)} papers, model {scored[0]['result'].llm_model})\n")
    L.append("| Measure | Raw model score | Adjusted score |\n|---|---|---|")
    L.append(f"| Spearman with human mean | {fmt(spearman(raw, h))} | {fmt(spearman(adj, h))} |")
    ci = spearman_ci(raw, h)
    L.append(f"| 95% bootstrap CI (raw) | {'undefined' if ci is None else f'{ci[0]:.2f} to {ci[1]:.2f}'} | |")
    L.append(f"| AUC accepted vs rejected | {fmt(auc(raw, acc))} | {fmt(auc(adj, acc))} |")
    L.append(f"| Mean score (human {np.mean(h):.2f}) | {np.mean(raw):.2f} | {np.mean(adj):.2f} |")
    L.append(f"| Distinct raw values | {len(set(raw))} | |")
    L.append("\nSpearman 0 = no relation, 1 = same ranking. AUC 0.5 = chance. A constant raw score (one distinct "
             "value) makes the correlation undefined: the model then does not discriminate between papers.\n")
    (BENCH / "summary.json").write_text(json.dumps({
        "llm_model": scored[0]["result"].llm_model, "n": len(scored), "dataset": "ICLR 2017 (PeerRead)",
        "spearman_raw": spearman(raw, h), "auc_raw": auc(raw, acc), "distinct_raw_values": len(set(raw))}), encoding="utf-8")
    dropped = [r["result"].stats.get("llm_comments_dropped", 0) for r in scored]
    kept = [sum(f.module in ("review", "critic", "novelty") for f in r["result"].findings) for r in scored]
    L.append(f"Unsupported LLM comments dropped by the evidence verifier: {sum(dropped)} (kept {sum(kept)}).\n")

    if do_fit and len(scored) >= 10:
        X = [feature_vector(r["result"].scores.raw_overall, r["result"].findings) for r in scored]
        s = fit_and_save(X, h, trained_on=f"{len(scored)} ICLR 2017 papers (PeerRead)")
        L.append("## Calibration fit (ridge regression, leave-one-out)\n")
        L.append(f"- Features: {', '.join(FEATURES)}\n- Ridge strength: {s['lam']}\n"
                 f"- Leave-one-out Spearman: {fmt(s['loo_spearman'])}\n"
                 f"- Leave-one-out MAE: {s['loo_mae']:.2f} (always predicting the mean: {s['baseline_mae']:.2f})\n"
                 f"- Weights (standardised): {s['weights']}\n"
                 f"- Used by the tool: {'YES' if s['usable'] else 'NO (below the 0.3 threshold, heuristic adjustment stays)'}\n")
    elif do_fit:
        L.append("## Calibration fit\n\nSkipped: needs at least 10 scored papers.\n")
    return "\n".join(L)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="results_llm")
    ap.add_argument("--fit", action="store_true")
    a = ap.parse_args()
    text = build_report(load(a.dir), a.fit)
    (BENCH / f"report_{a.dir}.md").write_text(text, encoding="utf-8")
    print(text)
