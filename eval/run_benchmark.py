"""Run the pipeline on benchmark papers and save one result file per paper (resumable).

Usage:
  python -m eval.run_benchmark --limit 20 --llm     # LLM review on 20 papers spread over the rating range
  python -m eval.run_benchmark                      # rule-based checks on every paper (fast)
"""
import argparse
import json
import time
from pathlib import Path

from core.llm.router import get_llm
from core.pipeline import run_pipeline
from core.storage.cache import Cache

BENCH = Path(__file__).resolve().parent.parent / "data" / "benchmark"


def pick_sample(papers: list[dict], limit: int | None) -> list[dict]:
    """Evenly spaced over the human-rating range, so the sample is not all accepts or all rejects."""
    ranked = sorted(papers, key=lambda p: (p["human_mean"], p["id"]))
    if not limit or limit >= len(ranked):
        return ranked
    step = (len(ranked) - 1) / (limit - 1) if limit > 1 else 0
    return [ranked[round(i * step)] for i in range(limit)]


def main(limit: int | None, use_llm: bool, online: bool) -> None:
    papers = json.loads((BENCH / "papers.json").read_text(encoding="utf-8"))
    sample = pick_sample(papers, limit)
    out_dir = BENCH / ("results_llm" if use_llm else "results_rules")
    out_dir.mkdir(exist_ok=True)
    llm = get_llm() if use_llm else None
    if use_llm and llm.name == "none":
        raise SystemExit("LLM_PROVIDER is none; set it to ollama in .env or run without --llm.")
    for i, p in enumerate(sample, 1):
        dest = out_dir / f"{p['id']}.json"
        if dest.exists():
            print(f"[{i}/{len(sample)}] {p['id']} done already")
            continue
        t = time.time()
        pdf = (BENCH / p["pdf"]).read_bytes()
        result = run_pipeline(pdf, p["id"] + ".pdf", online=online, cache=Cache(use_db=False), llm=llm)
        dest.write_text(json.dumps({"paper": p, "seconds": round(time.time() - t), "result": result.model_dump(mode="json")}),
                        encoding="utf-8")
        s = result.scores
        print(f"[{i}/{len(sample)}] {p['id']} human {p['human_mean']:.1f} | status {result.status} | "
              f"raw {s.raw_overall if s else None} | {time.time() - t:.0f}s", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--llm", action="store_true")
    ap.add_argument("--online", action="store_true", help="also run Crossref / related-paper searches (slow)")
    a = ap.parse_args()
    main(a.limit, a.llm, a.online)
