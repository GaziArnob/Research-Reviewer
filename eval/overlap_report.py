"""Overlap of tool findings with human review sentences, for the saved benchmark results.

Usage: python -m eval.overlap_report [--dir results_rules] [--dir results_llm] [--exclude-module writing ...]
"""
import argparse
import json

from eval.overlap import BENCH, evaluate, format_report, human_sentences


def load_tool(dir_name: str, exclude: set[str], kinds: set[str] | None) -> dict:
    tool = {}
    for f in sorted((BENCH / dir_name).glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        pid = d["paper"]["id"]
        tool[pid] = [(x["module"], x["text"]) for x in d["result"]["findings"]
                     if x["module"] not in exclude and (kinds is None or x["kind"] in kinds)]
    return tool


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", action="append", default=None)
    ap.add_argument("--exclude-module", action="append", default=[])
    ap.add_argument("--kinds", default="", help="comma list, e.g. major,minor,question (default: all)")
    a = ap.parse_args()
    kinds = set(a.kinds.split(",")) if a.kinds else None
    out = ["# Overlap with human reviews", ""]
    for d in a.dir or ["results_rules", "results_llm"]:
        tool = load_tool(d, set(a.exclude_module), kinds)
        human = {pid: human_sentences(pid) for pid in tool}
        out.append(format_report(d, evaluate(tool, human)))
        out.append("")
    text = "\n".join(out)
    (BENCH / "report_overlap.md").write_text(text, encoding="utf-8")
    print(text)
