"""Compare local models on the same benchmark papers with the same pipeline.

Per model: seconds per paper, JSON failures (stages whose answer could not be used), comments kept and the share dropped
by the quote check, and the overlap with the human reviews (see eval/overlap.py).

Usage:  python -m eval.compare_models --models qwen2.5:3b llama3.2:3b --limit 6
Models must already be pulled in Ollama (ollama pull <name>). Needs a few minutes per paper and model.
"""
import argparse
import json
import time

from core.llm.ollama_client import OllamaClient
from core.pipeline import run_pipeline
from core.storage.cache import Cache
from eval.overlap import BENCH, evaluate, format_report, human_sentences
from eval.run_benchmark import pick_sample


def run_model(model: str, sample: list[dict]) -> dict:
    llm = OllamaClient(model=model)
    tool, secs, fails, kept, dropped = {}, [], 0, 0, 0
    for p in sample:
        t = time.time()
        res = run_pipeline((BENCH / p["pdf"]).read_bytes(), p["id"] + ".pdf", online=False, cache=Cache(use_db=False), llm=llm)
        secs.append(time.time() - t)
        fails += sum("failed, not cached" in line for line in res.step_log)
        llm_f = [f for f in res.findings if f.module in ("review", "critic", "novelty")]
        kept += len(llm_f)
        dropped += res.stats.get("llm_comments_dropped", 0)
        tool[p["id"]] = [(f.module, f.text) for f in llm_f]
        print(f"  {model}: {p['id']} {secs[-1]:.0f}s, {len(llm_f)} LLM findings", flush=True)
    human = {pid: human_sentences(pid) for pid in tool}
    return {"model": model, "papers": len(sample), "sec_per_paper": sum(secs) / len(secs), "stage_failures": fails,
            "kept": kept, "dropped": dropped, "overlap": evaluate(tool, human)}


def main(models: list[str], limit: int) -> None:
    papers = json.loads((BENCH / "papers.json").read_text(encoding="utf-8"))
    sample = pick_sample(papers, limit)
    out = ["# Model comparison", "", f"{len(sample)} papers spread over the human rating range, LLM stages only (no online checks).", "",
           "| Model | s / paper | Stage failures | Kept | Dropped by quote check | Recall | Precision |", "|---|---|---|---|---|---|---|"]
    details = []
    for m in models:
        r = run_model(m, sample)
        o = r["overlap"]
        drop = r["dropped"] / max(1, r["kept"] + r["dropped"])
        out.append(f"| {m} | {r['sec_per_paper']:.0f} | {r['stage_failures']} | {r['kept']} | {drop:.0%} | "
                   f"{o['recall'][0.25]:.1%} (chance {o['recall_chance'][0.25]:.1%}) | {o['precision'][0.25]:.1%} (chance {o['precision_chance'][0.25]:.1%}) |")
        details.append(format_report(m, o))
    text = "\n".join(out + [""] + details)
    (BENCH / "report_models.md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", default=["qwen2.5:3b"])
    ap.add_argument("--limit", type=int, default=6)
    a = ap.parse_args()
    main(a.models, a.limit)
