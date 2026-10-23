from core.config import settings
from core.http import get
from core.models import RelatedPaper

URL = "https://api.openalex.org/works"


def search(query: str, limit: int = 8) -> list[RelatedPaper]:
    params = {"search": query[:300], "per-page": limit}
    if settings.contact_email:
        params["mailto"] = settings.contact_email
    r = get(URL, params=params)
    if r.status != 200 or not r.data:
        return []
    out = []
    for w in r.data.get("results", []):
        title = w.get("title") or w.get("display_name")
        if not title:
            continue
        venue = ((w.get("primary_location") or {}).get("source") or {}).get("display_name")
        out.append(RelatedPaper(title=title, year=w.get("publication_year"), venue=venue,
                                url=w.get("doi") or w.get("id"), source="openalex"))
    return out
