"""Download the TEXT of the official reviews for the benchmark papers (PeerRead, ICLR 2017).

Small JSON files straight from raw.githubusercontent.com (about 20 KB each), needed by eval/overlap.py.
Usage: python -m eval.fetch_reviews
"""
import json
import time

import requests

from eval.overlap import BENCH

RAW = "https://raw.githubusercontent.com/allenai/PeerRead/master/data/iclr_2017/{split}/reviews/{pid}.json"


def main() -> None:
    papers = json.loads((BENCH / "papers.json").read_text(encoding="utf-8"))
    out = BENCH / "reviews"
    out.mkdir(exist_ok=True)
    total = 0
    for p in papers:
        dest = out / f"{p['id']}.json"
        if dest.exists():
            continue
        split, _, pid = p["id"].partition("-")
        r = requests.get(RAW.format(split=split, pid=pid), headers={"User-Agent": "ResearchReviewer/0.1"}, timeout=30)
        r.raise_for_status()
        reviews = r.json().get("reviews", [])
        comments = [rv.get("comments", "") for rv in reviews
                    if "AnonReviewer" in (rv.get("OTHER_KEYS") or "") and not rv.get("IS_META_REVIEW")]
        dest.write_text(json.dumps({"paper_id": p["id"], "comments": comments}), encoding="utf-8")
        total += dest.stat().st_size
        time.sleep(0.1)
    print(f"saved review texts for {len(papers)} papers ({total / 1024:.0f} KB new) in {out}")


if __name__ == "__main__":
    main()
