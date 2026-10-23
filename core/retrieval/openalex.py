import re
from difflib import SequenceMatcher
from typing import Optional

from core.config import settings
from core.http import get
from core.models import RelatedPaper

URL = "https://api.openalex.org/works"


def _params(extra: dict) -> dict:
    p = dict(extra)
    if settings.contact_email:
        p["mailto"] = settings.contact_email
    return p


def abstract_text(inverted: Optional[dict]) -> Optional[str]:
    """OpenAlex ships abstracts as {word: [positions]}; put the words back in order."""
    if not inverted:
        return None
    slots = {pos: word for word, positions in inverted.items() for pos in positions}
    return " ".join(slots[i] for i in sorted(slots)) or None


def _to_paper(w: dict, source: str) -> Optional[RelatedPaper]:
    title = w.get("title") or w.get("display_name")
    if not title:
        return None
    venue = ((w.get("primary_location") or {}).get("source") or {}).get("display_name")
    return RelatedPaper(title=title, year=w.get("publication_year"), venue=venue, url=w.get("doi") or w.get("id"),
                        abstract=abstract_text(w.get("abstract_inverted_index")), source=source)


def search(query: str, limit: int = 8) -> list[RelatedPaper]:
    r = get(URL, params=_params({"search": query[:300], "per-page": limit}))
    if r.status != 200 or not r.data:
        return []
    return [p for w in r.data.get("results", []) if (p := _to_paper(w, "openalex"))]


def related_by_graph(title: str, limit: int = 10) -> list[RelatedPaper]:
    """Papers OpenAlex itself lists as related to this one (from its citation graph), found by exact-ish title.

    This is usually a better novelty reference set than a keyword search. Returns [] when the title is not found.
    """
    r = get(URL, params=_params({"search": title[:300], "per-page": 3}))
    if r.status != 200 or not r.data:
        return []
    norm = lambda t: re.sub(r"\W+", " ", (t or "").lower()).strip()
    hit = next((w for w in r.data.get("results", [])
                if SequenceMatcher(None, norm(w.get("title")), norm(title)).ratio() >= 0.9), None)
    ids = [i.rsplit("/", 1)[-1] for i in (hit or {}).get("related_works", [])[:limit]]
    if not ids:
        return []
    r2 = get(URL, params=_params({"filter": "openalex:" + "|".join(ids), "per-page": limit}))
    if r2.status != 200 or not r2.data:
        return []
    return [p for w in r2.data.get("results", []) if (p := _to_paper(w, "openalex-graph"))]
