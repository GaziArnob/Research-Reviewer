"""A small hand-labelled gold set: the only honest way to measure whether the tool's comments are right.

1. python -m eval.gold init --dir results_llm --papers 10 [--per-paper 12]
   writes data/gold/labels.csv with the tool's comments (one row each) and an empty `label` column.
2. Open the CSV in Excel / LibreOffice, read the comment and its quote in the paper, and write in `label`:
      ok     the comment is correct and worth a reviewer's time
      wrong  the comment is factually wrong about the paper, or the quote does not support it
      weak   true but trivial / vague / not useful
   Optionally add rows with module = "MISSED" and the problem in `text` for real issues the tool did not find.
3. python -m eval.gold score
   prints precision (ok / labelled) per check with a 95% interval, and how many real issues were missed.
Nothing is sent anywhere; the file stays in data/gold (git-ignored).
"""
import argparse
import csv
import json
from collections import defaultdict

from core.stats import wilson
from eval.overlap import BENCH

GOLD = BENCH.parent / "gold" / "labels.csv"
FIELDS = ["paper_id", "finding_id", "module", "kind", "text", "quote", "page", "label"]


def init(dir_name: str, papers: int, per_paper: int) -> int:
    GOLD.parent.mkdir(parents=True, exist_ok=True)
    if GOLD.exists():
        raise SystemExit(f"{GOLD} already exists: move it away first so labels are not overwritten.")
    rows = []
    for f in sorted((BENCH / dir_name).glob("*.json"))[:papers]:
        d = json.loads(f.read_text(encoding="utf-8"))
        shown = [x for x in d["result"]["findings"] if x["kind"] in ("major", "minor", "question") and x["module"] != "injection"]
        for x in shown[:per_paper]:
            ev = x["evidence"][0] if x["evidence"] else {}
            rows.append({"paper_id": d["paper"]["id"], "finding_id": x["id"], "module": x["module"], "kind": x["kind"],
                         "text": x["text"], "quote": ev.get("quote", ""), "page": ev.get("page", ""), "label": ""})
    with GOLD.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, FIELDS)
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def score(path=GOLD) -> str:
    by_mod = defaultdict(lambda: [0, 0])  # module -> [ok, labelled]
    missed = 0
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            label = (r.get("label") or "").strip().lower()
            if r["module"] == "MISSED":
                missed += 1
            elif label in ("ok", "wrong", "weak"):
                by_mod[r["module"]][1] += 1
                by_mod[r["module"]][0] += label == "ok"
    L = ["| Check | Labelled | OK | Precision (95% range) |", "|---|---|---|---|"]
    total_ok = total = 0
    for m, (ok, n) in sorted(by_mod.items(), key=lambda kv: -kv[1][1]):
        lo, hi = wilson(ok, n)
        L.append(f"| {m} | {n} | {ok} | {ok / n:.0%} ({lo:.0%}-{hi:.0%}) |")
        total_ok, total = total_ok + ok, total + n
    if total:
        lo, hi = wilson(total_ok, total)
        L.append(f"| **all** | {total} | {total_ok} | {total_ok / total:.0%} ({lo:.0%}-{hi:.0%}) |")
    L += ["", f"Real issues the tool missed (rows marked MISSED): {missed}"]
    if total and missed:
        L.append(f"Rough recall of real issues: {total_ok / (total_ok + missed):.0%}")
    return "\n".join(L)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("init")
    i.add_argument("--dir", default="results_llm")
    i.add_argument("--papers", type=int, default=10)
    i.add_argument("--per-paper", type=int, default=12)
    sub.add_parser("score")
    a = ap.parse_args()
    if a.cmd == "init":
        print(f"Wrote {init(a.dir, a.papers, a.per_paper)} rows to {GOLD}. Fill the `label` column, then run: python -m eval.gold score")
    else:
        print(score())
