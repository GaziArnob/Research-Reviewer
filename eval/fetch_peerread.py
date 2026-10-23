"""Download a benchmark of ICLR 2017 papers with their official reviewer ratings.

Why not the OpenReview API: it answers automated requests with a "challenge verification" (bot protection), and
this project does not work around that. PeerRead (Kang et al., 2018, github.com/allenai/PeerRead) republishes the
same ICLR 2017 OpenReview papers, reviews and accept/reject decisions as a public dataset.

Usage: python -m eval.fetch_peerread [--splits dev test]
"""
import argparse
import json
import time
from pathlib import Path

import requests

API = "https://api.github.com/repos/allenai/PeerRead/contents/data/iclr_2017/"
OUT = Path(__file__).resolve().parent.parent / "data" / "benchmark"
HEADERS = {"User-Agent": "ResearchReviewer/0.1"}


def _list(split: str, sub: str) -> list[dict]:
    r = requests.get(f"{API}{split}/{sub}", headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def official_ratings(review_json: dict) -> list[int]:
    """Numeric recommendations (1-10) given by the assigned anonymous reviewers, not by commenters or chairs."""
    out = []
    for rv in review_json.get("reviews", []):
        if rv.get("IS_META_REVIEW") or "AnonReviewer" not in (rv.get("OTHER_KEYS") or ""):
            continue
        if isinstance(rv.get("RECOMMENDATION"), (int, float)):
            out.append(int(rv["RECOMMENDATION"]))
    return out


def main(splits: list[str]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "pdfs").mkdir(exist_ok=True)
    papers, to_get = [], []
    for split in splits:
        pdfs = {p["name"]: p for p in _list(split, "pdfs")}
        for item in _list(split, "reviews"):
            data = requests.get(item["download_url"], headers=HEADERS, timeout=30).json()
            ratings = official_ratings(data)
            pdf = pdfs.get(item["name"].replace(".json", ".pdf"))
            if len(ratings) < 2 or pdf is None:
                continue
            papers.append({"id": f"{split}-{data['id']}", "title": data["title"], "accepted": bool(data["accepted"]),
                           "ratings": ratings, "human_mean": sum(ratings) / len(ratings), "split": split,
                           "pdf": f"pdfs/{split}-{pdf['name']}"})
            if not (OUT / papers[-1]["pdf"]).exists():
                to_get.append((pdf, OUT / papers[-1]["pdf"]))
            time.sleep(0.1)
    total_mb = sum(p["size"] for p, _ in to_get) / 1e6
    print(f"{len(papers)} papers with >=2 official ratings; downloading {len(to_get)} PDFs (~{total_mb:.0f} MB)")
    for pdf, dest in to_get:
        dest.write_bytes(requests.get(pdf["download_url"], headers=HEADERS, timeout=60).content)
        time.sleep(0.1)
    (OUT / "papers.json").write_text(json.dumps(papers, indent=1), encoding="utf-8")
    acc = sum(p["accepted"] for p in papers)
    print(f"saved {OUT / 'papers.json'} | accepted {acc}, rejected {len(papers) - acc}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--splits", nargs="+", default=["dev", "test"])
    main(ap.parse_args().splits)
